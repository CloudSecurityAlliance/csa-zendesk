"""Receiving the authorization code.

A one-shot loopback listener for the ordinary case, and a paste fallback for a
remote shell whose browser is on a different machine (ADR-009).

Both paths require a browser. OAuth's consent screen is where a human proves
who they are, and on an account with passkeys or biometrics that is the point
rather than an obstacle - there is no headless path that preserves it. What
`paste` changes is WHICH machine holds the browser, never whether one exists.

Every prompt goes to the stream the caller passes, which is stderr in practice.
Under stdio MCP, stdout is the JSON-RPC channel: a print() here would corrupt
the session, so nothing in this module ever calls it.

Zendesk pre-registers redirect URIs as a fixed array - it does not accept a
dynamically-bound port, and it does not accept the `urn:ietf:wg:oauth:2.0:oob`
out-of-band URN (not an absolute URL, so the client-registration form rejects
it). The three URIs actually registered on the live OAuth client are the
`/callback` path on ports 8765, 8766 and 8767, in that order, so `Listener`
binds the first of those that is free rather than asking the OS for an
ephemeral one, and the paste fallback reuses the first of them as a fixed
string a later task can hand to the token endpoint.
"""

from __future__ import annotations

import http.server
import os
import socket
import sys
import time
from collections.abc import Sequence
from types import TracebackType
from typing import TextIO
from urllib.parse import parse_qs, urlparse

from .. import exceptions as exc

# How long one `select()` may block before `wait()` gets control back. Not a timeout anyone
# waits for - it only bounds how long a Ctrl-C sits undelivered, because CPython runs signal
# handlers between bytecodes and cannot while blocked in a C-level socket wait. Measured on
# Windows with `_thread.interrupt_main()`, the same call the real SIGINT handler makes: with one
# 8s blocking call the interrupt surfaced after 8.00s; with a 0.5s slice, after 1.03s (#79).
#
# 0.5s because it is imperceptible to a person pressing Ctrl-C and costs at most two wakeups a
# second while a sign-in is pending.
_POLL_SECONDS = 0.5


class _OneShotServer(http.server.HTTPServer):
    """`HTTPServer`, minus a reuse flag that means the opposite thing on Windows.

    `socketserver.TCPServer` sets `allow_reuse_address = 1`, which on POSIX means "rebind
    a port still in TIME_WAIT" - harmless and necessary, because a listener that just shut
    down would otherwise refuse to come back for a minute. On Windows `SO_REUSEADDR` means
    something else entirely: it permits binding a port ANOTHER SOCKET IS ACTIVELY
    LISTENING ON, and the later bind wins subsequent connections.

    For a loopback OAuth callback that is not a portability wrinkle, it is a hijack: any
    local process could bind 8765 and receive the authorization code this listener is
    waiting for. PKCE and the `state` check are what stop that being a full compromise,
    which is the reason this is a weakened layer rather than an open door - but the layer
    is supposed to be there.

    So: reuse on POSIX, `SO_EXCLUSIVEADDRUSE` on Windows, which is the option that actually
    means "this port is mine". The visible consequence is that binding an occupied port now
    FAILS on Windows, which is what the candidate-port loop was written expecting all along.
    """

    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        # `sys.platform`, not `os.name`: mypy narrows the first, so the POSIX stubs
        # (which have no SO_EXCLUSIVEADDRUSE) never see this line.
        if sys.platform == "win32":  # pragma: no cover - Windows-only
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


__all__ = ["CallbackError", "Listener", "paste_fallback", "PASTE_REDIRECT"]

#: Ports registered as redirect URIs on the live Zendesk OAuth client, in the
#: order `Listener` tries them. Do not reorder or extend this without also
#: updating the client's registered redirect URIs in Zendesk - a port that is
#: free locally but unregistered there fails authorization, not this bind.
_CANDIDATE_PORTS: tuple[int, ...] = (8765, 8766, 8767)

#: The first candidate, exported so the paste fallback - which has no listening
#: socket and therefore no bound port to report - and a later task's token
#: exchange can agree on the same fixed redirect_uri string.
PASTE_REDIRECT = "http://127.0.0.1:8765/callback"

_PAGE = b"<html><body><p>Authorised. You can close this tab.</p></body></html>"


class CallbackError(exc.ZendeskError):
    """The authorization callback did not deliver a usable code."""


class Listener:
    """A loopback HTTP server that accepts exactly one callback request.

    The socket is bound in `__init__`, not `__enter__`: construction either
    succeeds with a bound, listening socket, or raises `CallbackError` and
    leaves nothing to close - so `__exit__` can assume a real server exists
    whenever it runs.

    `wait` does the accepting itself, synchronously, via a single
    `HTTPServer.handle_request()` call - there is no background thread. That
    call returns after handling exactly one connection (or after `timeout`
    elapses with none), which is what makes this a one-shot listener rather
    than an open port that keeps accepting for as long as the process runs.
    """

    def __init__(self, *, state: str, ports: Sequence[int] = _CANDIDATE_PORTS) -> None:
        self._state = state
        self._result: str | None = None
        self._error: str | None = None
        self._got_request = False
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib's name
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path != "/callback":
                    outer._error = f"the callback arrived on an unexpected path: {parsed.path!r}"
                elif "error" in query:
                    reported = (query.get("error") or [""])[0]
                    outer._error = f"Zendesk refused the authorization: {reported}"
                elif (query.get("state") or [""])[0] != outer._state:
                    outer._error = "the callback carried the wrong state parameter; refusing it"
                else:
                    code = (query.get("code") or [""])[0]
                    if code:
                        outer._result = code
                    else:
                        outer._error = "the callback had no `code` parameter"
                outer._got_request = True
                # The code is a credential and must never reach a log line - the
                # HTML response is static, and the access log is silenced below,
                # so this is the only place it is handled and nothing writes it
                # anywhere but into `outer._result`, in memory, for this process.
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(_PAGE)

            def log_message(self, format: str, *args: object) -> None:
                """Silence the stdlib's stderr access log. The default
                implementation writes the full request line - query string and
                all - to stderr, which would put the authorization code
                wherever stderr goes (a terminal, a log file)."""

        server: http.server.HTTPServer | None = None
        for port in ports:
            try:
                server = _OneShotServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        if server is None:
            tried = ", ".join(str(p) for p in ports)
            raise CallbackError(f"every candidate callback port is already in use ({tried}); free one and retry")
        self._server = server
        self._handler_cls = Handler

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/callback"

    def __enter__(self) -> Listener:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._server.server_close()

    def wait(self, timeout: float) -> str:
        """Block for exactly one callback request, or `timeout` seconds.

        Calls `handle_request()` in `_POLL_SECONDS` slices until one callback
        has been handled or `timeout` has elapsed. Each call accepts and fully
        handles one connection before returning - or, if none arrives within
        the slice, returns having handled none, which is why the loop condition
        is `_got_request` rather than a call count. Either way this method
        returns; a hung authorization does not hang the caller forever.

        Slices rather than one long call because CPython runs signal handlers
        between bytecodes and cannot while blocked in a C-level socket wait, so
        a single `handle_request(timeout=300)` swallowed Ctrl-C for up to five
        minutes and the only way out of `auth login` was to close the terminal
        (#79). Measured on Windows with `_thread.interrupt_main()`: 8.00s to
        surface with one 8s call, 1.03s with a 0.5s slice.

        `HTTPServer.timeout` bounds only the `select()` before `accept()` - it
        says nothing about a connection once accepted. `BaseHTTPRequestHandler
        .timeout` defaults to `None`, so `rfile.readline()` on an accepted
        connection that never sends a request line blocks forever: any process
        that opens a TCP connection to this loopback port without sending
        bytes - a browser's speculative connection, a local dev tool, a port
        scanner - would consume the one shot and hang here past `timeout`,
        contradicting the promise above and, on the live 300s default, hanging
        `auth login` indefinitely. Setting the handler class's own `timeout`
        closes that: `StreamRequestHandler.setup()` applies it via
        `connection.settimeout()` when it is not `None`, and the stdlib's
        `handle_one_request()` already catches `socket.timeout` and simply
        closes the connection rather than raising - so a stalled connection
        ends this call the same way "nothing arrived at all" does (`_got_request`
        stays `False`), rather than hanging it forever.

        The read budget is `timeout`, not the poll slice and not "whatever is
        left of it". The slice bounds only how long one `select()` may block;
        it is a responsiveness knob, not a deadline anyone waits for. A
        connection arriving is not this call spending less of its budget, it is
        this call *starting* to spend it on the accepted connection - so the
        handler keeps the full `timeout`, and cutting it to the slice would
        abandon a real callback that was merely slow to send its request line.

        What bounds the whole thing is the deadline: at most `timeout` of
        slices, then at most one read budget for a connection accepted just
        before it expired. So one `wait(timeout)` is still bounded by a small,
        fixed multiple of `timeout` in the worst case rather than by nothing at
        all - the same conclusion as before the loop, reached from the deadline
        rather than from a single call's timeout.
        """
        # The HANDLER keeps the full budget: once a connection is accepted this is the read
        # timeout, and cutting it to the poll slice would abandon a real callback that was
        # merely slow to send its request line.
        self._handler_cls.timeout = timeout
        # The SERVER gets the slice, so each `handle_request()` returns promptly and a pending
        # KeyboardInterrupt is delivered between calls rather than after `timeout` (#79). Before
        # this, Ctrl-C during `auth login` did nothing for up to five minutes and the only way
        # out was to close the terminal.
        self._server.timeout = _POLL_SECONDS
        deadline = time.monotonic() + timeout
        # One-shot is preserved by the `_got_request` condition, not by calling once: a slice
        # that times out handled nothing, so the loop is still waiting for its single callback.
        while not self._got_request and time.monotonic() < deadline:
            self._server.handle_request()
        if not self._got_request:
            # Says the link is DEAD, not merely that we stopped waiting (#48).
            # The listener closes here, so a sign-in completed after this point
            # redirects to a socket nobody is bound to and the browser reports
            # "can't connect to the server" - which reads as a broken machine
            # rather than an expired attempt. Observed: that error surfaced long
            # afterwards with nothing connecting it back to this timeout.
            raise CallbackError(
                f"no callback arrived within {timeout:.0f}s, so the login window has closed "
                f"and the browser link is now dead - completing sign-in in that tab will show "
                f"a connection error rather than finishing. Run `authenticate` again to get a "
                f"fresh link, and complete it within {timeout:.0f}s. A browser is required - "
                f"if none can open on this machine, run `csa-zendesk auth login --paste` in a "
                f"terminal instead and finish sign-in in a browser elsewhere; all surfaces share "
                f"the same credential file, so that fixes this session too."
            )
        if self._error is not None:
            raise CallbackError(self._error)
        if self._result is None:  # pragma: no cover - guarded by _error above
            # A `raise`, not an `assert`: `python -O` strips asserts entirely, and
            # this one is the last thing between a None and a caller annotated to
            # receive a str. The invariant it restates (a request arrived, no error
            # was recorded, therefore a result exists) holds today - but an
            # invariant that only holds today is exactly what a guard is for, and a
            # guard that disappears under a common interpreter flag is not one.
            # Also what bandit B101 is pointing at, so the gate goes green because
            # the code improved rather than because the finding was silenced.
            raise CallbackError("the callback server recorded neither a result nor an error")
        return self._result


def paste_fallback(*, prompt_to: TextIO, read_from: TextIO, state: str) -> str:
    """Read the redirect URL the operator pasted. For a remote shell with no
    browser reachable at any loopback port (ADR-009) - the operator opens the
    authorization URL themselves, lets the browser fail to connect to the
    unreachable redirect, and copies the address bar contents back here."""
    prompt_to.write("Paste the full redirect URL from the browser: ")
    prompt_to.flush()
    line = read_from.readline().strip()
    query = parse_qs(urlparse(line).query)
    if (query.get("state") or [""])[0] != state:
        raise CallbackError("the pasted URL carried the wrong state parameter; refusing it")
    code = (query.get("code") or [""])[0]
    if not code:
        raise CallbackError("the pasted URL has no `code` parameter")
    return code
