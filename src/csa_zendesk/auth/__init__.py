"""OAuth. ADR-009 (the flow) and ADR-015 (it is the only one).

This module is the package's public surface for authentication: `login`,
`whoami`, `access_token`, and the error types a caller of those needs to
catch. Every private module (`_pkce`, `_store`, `_callback`, `_flow`) stays
private - nothing outside this file imports them directly.

**No out-of-band redirect.** `urn:ietf:wg:oauth:2.0:oob` is not an absolute
URL, so Zendesk's OAuth client registration form rejects it - it is not one of
the three redirect URIs actually registered on the live client. `login`'s
paste fallback instead reuses `_callback.PASTE_REDIRECT`
(`http://127.0.0.1:8765/callback`, the first of those three): the operator
opens the authorization URL, Zendesk redirects the browser to that loopback
address, nothing is listening there so the connection fails, and the operator
copies the `code` parameter out of the browser's address bar. PKCE is what
makes that safe - the code is useless without the verifier that never left
this machine.

**This module's own prompts go to stderr, never stdout - but `webbrowser.open()`
is a caveat that guarantee does not fully cover.** `login()`'s non-paste path
calls `webbrowser.open(url)` (smaller item, final whole-branch review): on a
desktop with a graphical browser registered, that call launches a separate
process and returns immediately, touching neither of this process's own
stdio streams. But `webbrowser` falls back to a CONSOLE browser (`lynx`,
`w3m`, ...) when no graphical one is registered or `$DISPLAY`/`$BROWSER`
point at one, and a console browser is a child process that inherits this
process's stdio by default - so it can read from and write to the SAME
stdin/stdout a stdio MCP server's JSON-RPC session is carried on. This
module's own "never prints to stdout" guarantee is about code in this
package; it says nothing about a child process this package spawns via the
standard library. Not reachable in the common desktop case this server
targets, and no host running `csa-zendesk-mcp` today runs headless with only
a console browser registered - but an embedder who does hits this, and the
failure mode (a corrupted JSON-RPC stream, `server.py`'s module docstring)
looks nothing like "picked the wrong browser."
"""

from __future__ import annotations

import os
import secrets
import sys
import webbrowser
from collections.abc import Sequence

import httpx

from ._callback import PASTE_REDIRECT, CallbackError, Listener, paste_fallback
from ._flow import (
    AuthExchangeError,
    NotAuthorised,
    RevokeError,
    ScopeError,
    TokenAlreadyInvalid,
    access_token,
    exchange_code,
    revoke,
)
from ._pkce import authorize_url, challenge_for, new_verifier
from ._store import TokenFileError, Tokens, clear, read, token_path, write
from .whoami import NotAuthenticated, whoami

__all__ = [
    "AuthExchangeError",
    "CallbackError",
    "NotAuthenticated",
    "NotAuthorised",
    "RevokeError",
    "ScopeError",
    "TokenAlreadyInvalid",
    "TokenFileError",
    "Tokens",
    "access_token",
    "clear",
    "login",
    "logout",
    "read",
    "revoke",
    "token_path",
    "whoami",
]


def _required_env(name: str, *, hint: str) -> str:
    """A required environment variable, or a `NotAuthorised` that says what to
    set. Never a bare `KeyError` - every other entry point in this package
    (`_flow.access_token`) fails closed on a missing variable with a message
    naming it and what to do; `login` is a user-facing entry point too, so it
    matches rather than surfacing a stack trace as the first thing a new
    operator sees."""
    value = os.environ.get(name, "")
    if not value:
        raise NotAuthorised(f"{name} is not set. {hint} There is no default.")
    return value


def login(
    *,
    scopes: Sequence[str],
    open_browser: bool = True,
    paste: bool = False,
    timeout: float = 300.0,
    transport: httpx.BaseTransport | None = None,
) -> Tokens:
    """Run the authorization-code + PKCE flow once, end to end, and persist
    the result.

    Two paths, chosen by `paste`:

    - `paste=False` (default): a one-shot loopback `Listener` binds a
      registered redirect port, `login` opens (or, with `open_browser=False`,
      just prints) the authorization URL, and waits for the browser's
      redirect to deliver the code.
    - `paste=True`: for a remote shell with no browser that can reach this
      machine's loopback address. The authorization URL is printed for the
      operator to open elsewhere; the redirect fails to connect, and the
      operator pastes the failed URL back in, from which the code is read.

    Every prompt goes to stderr, never stdout - under stdio MCP, stdout is the
    JSON-RPC channel. `transport` exists only so tests can inject
    `httpx.MockTransport`; production callers never pass it.
    """
    subdomain = _required_env(
        "CSA_ZENDESK_SUBDOMAIN",
        hint="Set it to the Zendesk subdomain this server talks to (the 'example' in example.zendesk.com).",
    )
    client_id = _required_env(
        "CSA_ZENDESK_MCP_SERVER_IDENTIFIER",
        hint="Register a public OAuth client in Zendesk Admin Center (no secret is needed) and set its id.",
    )
    verifier = new_verifier()
    state = secrets.token_urlsafe(16)
    challenge = challenge_for(verifier)

    if paste:
        url = authorize_url(
            subdomain=subdomain,
            client_id=client_id,
            redirect_uri=PASTE_REDIRECT,
            scopes=scopes,
            challenge=challenge,
            state=state,
        )
        print(  # noqa: T201 - stderr, never stdout: stdout is the MCP JSON-RPC channel
            f"Open this URL, authorise, then paste the URL your browser lands on "
            f"(the connection will fail - that's expected):\n{url}\n",
            file=sys.stderr,
        )
        code = paste_fallback(prompt_to=sys.stderr, read_from=sys.stdin, state=state)
        redirect_uri = PASTE_REDIRECT
    else:
        with Listener(state=state) as listener:
            url = authorize_url(
                subdomain=subdomain,
                client_id=client_id,
                redirect_uri=listener.redirect_uri,
                scopes=scopes,
                challenge=challenge,
                state=state,
            )
            print(f"Opening your browser to authorise:\n{url}\n", file=sys.stderr)  # noqa: T201 - stderr, not stdout
            if open_browser:
                webbrowser.open(url)
            code = listener.wait(timeout)
            redirect_uri = listener.redirect_uri

    tokens = exchange_code(
        subdomain=subdomain,
        client_id=client_id,
        code=code,
        verifier=verifier,
        redirect_uri=redirect_uri,
        requested_scopes=list(scopes),
        transport=transport,
    )
    write(tokens)
    return tokens


def logout(*, transport: httpx.BaseTransport | None = None) -> str:
    """Revoke the stored token server-side, then clear the local file - in
    that order, never reversed. If revocation fails and the file were cleared
    first, the credential would be live with nothing left on this machine
    that could still revoke it.

    Returns one of three outcome strings, for `cli.py` to report on; never
    raises for "no token file" - logging out when already logged out is not
    an error, symmetric with how `login` is the thing to run when logged out:

    - `"no-token"` - nothing was on disk to log out of.
    - `"already-invalid"` - the server-side revoke was refused because the
      token was already invalid or expired (`TokenAlreadyInvalid`); the local
      file is cleared anyway, since a dead credential leaves nothing to
      protect.
    - `"revoked"` - the server-side revoke succeeded; the local file is
      cleared.

    Revoking the access token this way also invalidates its paired refresh
    token - confirmed 2026-09-19 against the live tenant, see `_flow.revoke`'s
    docstring and TODO.md E20. A stolen token file cannot outlive a `logout`.

    Any other failure (`RevokeError`, or `exc.ApiError` for a transport
    failure) propagates instead of returning, and the local file is
    deliberately left untouched - see `_flow.revoke`'s docstring for why.
    """
    tokens = read()
    if tokens is None:
        return "no-token"
    subdomain = _required_env(
        "CSA_ZENDESK_SUBDOMAIN",
        hint="Set it to the Zendesk subdomain this server talks to (the 'example' in example.zendesk.com).",
    )
    try:
        revoke(subdomain=subdomain, tokens=tokens, transport=transport)
    except TokenAlreadyInvalid:
        clear()
        return "already-invalid"
    clear()
    return "revoked"
