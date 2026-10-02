"""Tests for the loopback callback listener and the paste fallback.

`test_it_binds_a_loopback_port_and_reports_it` asserts membership in the exact
set of registered redirect URIs, not a `startswith("http://127.0.0.1:")` prefix
check - a dynamically-bound port would also pass a prefix check, and Zendesk's
pre-registered redirect array would still reject it. See the controller
amendment on the task 3 brief.
"""

import _thread
import io
import socket
import threading
import time
import urllib.request

import pytest

from csa_zendesk.auth import _callback

_REGISTERED_REDIRECT_URIS = {
    "http://127.0.0.1:8765/callback",
    "http://127.0.0.1:8766/callback",
    "http://127.0.0.1:8767/callback",
}

# A generous upper bound for tests where a background thread delivers the
# request - not testing timeout behaviour itself, just guarding against a
# genuine hang, so it can be short without risking flakiness under scheduler
# jitter. `handle_request()` returns as soon as the connection is accepted,
# typically in well under a millisecond, so none of these tests actually wait
# this long in practice.
_DELIVERY_TIMEOUT = 2.0

# The one test that asserts timeout behaviour itself uses a much smaller bound
# (tens of milliseconds) - the code path exercised is identical at any
# magnitude, so there is no reason to pay seconds of wall-clock time for it.
_NO_DELIVERY_TIMEOUT = 0.05


def test_it_binds_a_loopback_port_and_reports_it():
    with _callback.Listener(state="st") as listener:
        assert listener.redirect_uri in _REGISTERED_REDIRECT_URIS


def test_it_binds_loopback_only_not_every_interface():
    # Two lines guarding a real property, not inspection alone: a listener
    # bound to 0.0.0.0 would accept the authorization code from anywhere on
    # the network, not just this machine.
    with _callback.Listener(state="st") as listener:
        assert listener._server.server_address[0] == "127.0.0.1"


def test_a_connection_that_never_sends_a_request_does_not_hang_wait_forever():
    # HTTPServer.timeout bounds only select() before accept() - the accepted
    # connection's rfile.readline() has no timeout of its own by default and
    # blocks forever on a TCP connect that sends no bytes (a browser's
    # speculative connection, a local dev tool, a port scanner). This is the
    # regression test for that: without a handler-level socket timeout, this
    # test would hang the suite rather than fail it.
    with _callback.Listener(state="st") as listener:
        stalled = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            stalled.connect(("127.0.0.1", listener._server.server_port))
            start = time.monotonic()
            with pytest.raises(_callback.CallbackError, match="no callback arrived"):
                listener.wait(timeout=_NO_DELIVERY_TIMEOUT)
            elapsed = time.monotonic() - start
        finally:
            stalled.close()
    # Bounded by a small multiple of the timeout given, not by nothing at all.
    assert elapsed < 2.0


def test_log_message_never_writes_the_authorization_code_to_stderr(capsys):
    # An empty log_message override is invisible to a coverage report - the
    # line still "executes" - so this asserts the property the override
    # exists for, not merely that the method runs. The stdlib default writes
    # the full request line, query string included, to stderr; the code in
    # that query string is a credential (see the module and do_GET docstrings).
    with _callback.Listener(state="st") as listener:

        def deliver():
            url = listener.redirect_uri + "?code=DO-NOT-LOG-ME&state=st"
            urllib.request.urlopen(url, timeout=_DELIVERY_TIMEOUT).read()

        threading.Thread(target=deliver, daemon=True).start()
        assert listener.wait(timeout=_DELIVERY_TIMEOUT) == "DO-NOT-LOG-ME"
    captured = capsys.readouterr()
    assert "DO-NOT-LOG-ME" not in captured.err
    assert "DO-NOT-LOG-ME" not in captured.out


def test_it_returns_the_code_the_browser_delivers():
    with _callback.Listener(state="st") as listener:

        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?code=THE-CODE&state=st", timeout=_DELIVERY_TIMEOUT).read()

        threading.Thread(target=deliver, daemon=True).start()
        assert listener.wait(timeout=_DELIVERY_TIMEOUT) == "THE-CODE"


def test_a_mismatched_state_is_refused():
    # CSRF: a callback we did not initiate must not be accepted.
    with _callback.Listener(state="expected") as listener:

        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?code=X&state=attacker", timeout=_DELIVERY_TIMEOUT).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="state"):
            listener.wait(timeout=_DELIVERY_TIMEOUT)


def test_an_error_response_is_surfaced_not_swallowed():
    with _callback.Listener(state="st") as listener:

        def deliver():
            url = listener.redirect_uri + "?error=access_denied&state=st"
            urllib.request.urlopen(url, timeout=_DELIVERY_TIMEOUT).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="access_denied"):
            listener.wait(timeout=_DELIVERY_TIMEOUT)


def test_a_request_on_the_wrong_path_is_refused_not_hung():
    with _callback.Listener(state="st") as listener:
        base = listener.redirect_uri.rsplit("/callback", 1)[0]

        def deliver():
            urllib.request.urlopen(base + "/favicon.ico?state=st", timeout=_DELIVERY_TIMEOUT).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="path"):
            listener.wait(timeout=_DELIVERY_TIMEOUT)


def test_a_callback_with_no_code_and_no_error_is_refused():
    with _callback.Listener(state="st") as listener:

        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?state=st", timeout=_DELIVERY_TIMEOUT).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="code"):
            listener.wait(timeout=_DELIVERY_TIMEOUT)


def test_a_wait_with_nothing_delivered_times_out_rather_than_hanging():
    with _callback.Listener(state="st") as listener:
        with pytest.raises(_callback.CallbackError, match="no callback arrived"):
            listener.wait(timeout=_NO_DELIVERY_TIMEOUT)


def test_every_candidate_port_occupied_names_all_three_in_the_error():
    ports = (8765, 8766, 8767)
    sockets = []
    try:
        for port in ports:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(s)  # appended before bind so a failed bind still gets closed
            # SO_EXCLUSIVEADDRUSE on Windows, SO_REUSEADDR elsewhere. With SO_REUSEADDR
            # here, Windows lets the Listener bind straight over these sockets and the
            # test occupied nothing - it asserted a refusal whose precondition did not
            # exist. The two flags are not variants of one idea; see `_OneShotServer`.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.listen(1)
        with pytest.raises(_callback.CallbackError) as exc_info:
            _callback.Listener(state="st")
        for port in ports:
            assert str(port) in str(exc_info.value)
    finally:
        for s in sockets:
            s.close()


def test_a_response_never_carries_the_code_back_to_the_browser():
    bodies: list[bytes] = []
    with _callback.Listener(state="st") as listener:

        def deliver():
            url = listener.redirect_uri + "?code=SECRET-CODE&state=st"
            bodies.append(urllib.request.urlopen(url, timeout=_DELIVERY_TIMEOUT).read())

        thread = threading.Thread(target=deliver, daemon=True)
        thread.start()
        assert listener.wait(timeout=_DELIVERY_TIMEOUT) == "SECRET-CODE"
        thread.join(timeout=_DELIVERY_TIMEOUT)
    assert bodies and b"SECRET-CODE" not in bodies[0]


def test_the_paste_fallback_reads_a_pasted_url_and_prompts_on_stderr():
    err = io.StringIO()
    stdin = io.StringIO("http://localhost/callback?code=PASTED&state=st\n")
    assert _callback.paste_fallback(prompt_to=err, read_from=stdin, state="st") == "PASTED"
    assert err.getvalue()  # the prompt exists


def test_the_paste_fallback_refuses_a_mismatched_state():
    err = io.StringIO()
    stdin = io.StringIO("http://localhost/callback?code=X&state=wrong\n")
    with pytest.raises(_callback.CallbackError, match="state"):
        _callback.paste_fallback(prompt_to=err, read_from=stdin, state="st")


def test_the_paste_fallback_refuses_a_url_with_no_code():
    err = io.StringIO()
    stdin = io.StringIO("http://localhost/callback?state=st\n")
    with pytest.raises(_callback.CallbackError, match="code"):
        _callback.paste_fallback(prompt_to=err, read_from=stdin, state="st")


def test_paste_redirect_is_the_first_registered_uri():
    assert _callback.PASTE_REDIRECT == "http://127.0.0.1:8765/callback"
    assert _callback.PASTE_REDIRECT in _REGISTERED_REDIRECT_URIS


def test_a_pending_interrupt_lands_within_a_poll_slice_not_after_the_timeout():
    """#79: `auth login` could not be abandoned - Ctrl-C did nothing for up to five minutes.

    CPython runs signal handlers between bytecodes and cannot while blocked in a C-level
    socket wait, so a single `handle_request(timeout=300)` swallowed the interrupt until it
    returned. `wait()` now polls in `_POLL_SECONDS` slices.

    `_thread.interrupt_main()` is the call CPython's own SIGINT handler makes, so this is the
    real delivery path. Measured before the fix: 8.00s to surface with one 8s call, 1.03s with
    a 0.5s slice.

    This test is the regression guard and would fail on the pre-fix code: the timeout below is
    deliberately far larger than the assertion window.
    """
    timeout = 4.0
    with _callback.Listener(state="st") as listener:
        timer = threading.Timer(0.3, _thread.interrupt_main)
        timer.start()
        start = time.monotonic()
        try:
            listener.wait(timeout=timeout)
        except KeyboardInterrupt:
            elapsed = time.monotonic() - start
        except _callback.CallbackError:  # pragma: no cover - only if the interrupt is lost
            timer.cancel()
            pytest.fail(
                f"the interrupt never arrived: wait() ran its full {timeout}s budget, which is "
                f"the #79 behaviour this test exists to catch"
            )
        else:  # pragma: no cover - wait() cannot return without a callback
            timer.cancel()
            pytest.fail("wait() returned a code, but no callback was delivered")
        finally:
            timer.cancel()

    # A LITERAL bound, not a multiple of `_callback._POLL_SECONDS`. The first version used the
    # constant, so against the pre-fix code it failed with AttributeError - the constant does
    # not exist there - rather than on the timing it exists to measure. A test that fails for
    # the wrong reason still goes red, which is exactly how a guard stops meaning what its name
    # says. 1.5s is generous for a 0.5s slice on a loaded machine and far below the 4s deadline.
    assert elapsed < 1.5, (
        f"interrupt surfaced after {elapsed:.2f}s, not promptly - that is the #79 behaviour: "
        f"the signal waited for the {timeout}s call to return instead of landing between "
        f"polls"
    )


def test_the_handler_keeps_the_full_read_budget_not_the_poll_slice():
    """The slice bounds `select()`, never the read of a connection already accepted.

    Easy to lose while fixing #79, and nothing else here would notice: setting the handler's
    timeout to the poll slice would abandon a real callback that was merely slow to send its
    request line, which is exactly the hang the handler timeout was introduced to prevent.
    """
    timeout = 7.0
    with _callback.Listener(state="st") as listener:
        # wait() assigns both before polling; run it briefly and inspect what it set.
        timer = threading.Timer(0.2, _thread.interrupt_main)
        timer.start()
        try:
            listener.wait(timeout=timeout)
        except (KeyboardInterrupt, _callback.CallbackError):
            pass
        finally:
            timer.cancel()

        assert listener._handler_cls.timeout == timeout, (
            "the handler timeout is the read budget for an accepted connection and must stay the full timeout"
        )
        assert listener._server.timeout == _callback._POLL_SECONDS, (
            "the server timeout is the select() slice, so an interrupt lands promptly"
        )
