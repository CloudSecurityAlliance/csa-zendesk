"""Tests for the loopback callback listener and the paste fallback.

`test_it_binds_a_loopback_port_and_reports_it` asserts membership in the exact
set of registered redirect URIs, not a `startswith("http://127.0.0.1:")` prefix
check - a dynamically-bound port would also pass a prefix check, and Zendesk's
pre-registered redirect array would still reject it. See the controller
amendment on the task 3 brief.
"""

import io
import socket
import threading
import urllib.request

import pytest

from csa_zendesk.auth import _callback

_REGISTERED_REDIRECT_URIS = {
    "http://127.0.0.1:8765/callback",
    "http://127.0.0.1:8766/callback",
    "http://127.0.0.1:8767/callback",
}


def test_it_binds_a_loopback_port_and_reports_it():
    with _callback.Listener(state="st") as listener:
        assert listener.redirect_uri in _REGISTERED_REDIRECT_URIS


def test_it_returns_the_code_the_browser_delivers():
    with _callback.Listener(state="st") as listener:

        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?code=THE-CODE&state=st", timeout=5).read()

        threading.Thread(target=deliver, daemon=True).start()
        assert listener.wait(timeout=5) == "THE-CODE"


def test_a_mismatched_state_is_refused():
    # CSRF: a callback we did not initiate must not be accepted.
    with _callback.Listener(state="expected") as listener:

        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?code=X&state=attacker", timeout=5).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="state"):
            listener.wait(timeout=5)


def test_an_error_response_is_surfaced_not_swallowed():
    with _callback.Listener(state="st") as listener:

        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?error=access_denied&state=st", timeout=5).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="access_denied"):
            listener.wait(timeout=5)


def test_a_request_on_the_wrong_path_is_refused_not_hung():
    with _callback.Listener(state="st") as listener:
        base = listener.redirect_uri.rsplit("/callback", 1)[0]

        def deliver():
            urllib.request.urlopen(base + "/favicon.ico?state=st", timeout=5).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="path"):
            listener.wait(timeout=5)


def test_a_callback_with_no_code_and_no_error_is_refused():
    with _callback.Listener(state="st") as listener:

        def deliver():
            urllib.request.urlopen(listener.redirect_uri + "?state=st", timeout=5).read()

        threading.Thread(target=deliver, daemon=True).start()
        with pytest.raises(_callback.CallbackError, match="code"):
            listener.wait(timeout=5)


def test_a_wait_with_nothing_delivered_times_out_rather_than_hanging():
    with _callback.Listener(state="st") as listener:
        with pytest.raises(_callback.CallbackError, match="no callback arrived"):
            listener.wait(timeout=0.2)


def test_every_candidate_port_occupied_names_all_three_in_the_error():
    ports = (8765, 8766, 8767)
    sockets = []
    try:
        for port in ports:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(s)  # appended before bind so a failed bind still gets closed
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
            bodies.append(urllib.request.urlopen(url, timeout=5).read())

        thread = threading.Thread(target=deliver, daemon=True)
        thread.start()
        assert listener.wait(timeout=5) == "SECRET-CODE"
        thread.join(timeout=5)
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
