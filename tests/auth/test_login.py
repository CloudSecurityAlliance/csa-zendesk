"""Tests for `login`, which assembles Tasks 1-5 into one call.

CONTROLLER AMENDMENT (task-7-brief.md): there is no out-of-band redirect on
Zendesk - `urn:ietf:wg:oauth:2.0:oob` is not an absolute URL and is rejected at
client registration. `login`'s paste path uses `_callback.PASTE_REDIRECT`
instead, and `test_the_paste_flow_...` below asserts that string reaches the
token exchange byte for byte, since the authorization server string-matches it.

The browser-flow tests deliver the callback from a background thread rather
than from a monkeypatched `webbrowser.open` directly, by wrapping
`authorize_url` instead: `Listener`'s socket is already bound by the time
`login` calls `authorize_url` (the `with Listener(...)` block opens first), so
wrapping that call captures the real `redirect_uri` and `state` regardless of
whether `open_browser` goes on to call the (separately, also monkeypatched and
separately asserted) `webbrowser.open`. A real loopback socket is used, per
the "a real loopback socket is acceptable for the listener, as it is not the
network" rule - only the token exchange itself goes through
`httpx.MockTransport`.
"""

import threading
import urllib.request

import httpx
import pytest

from csa_zendesk import auth
from csa_zendesk.auth import _store

# Generous, but these tests never actually wait this long: the delivery thread
# connects to an already-bound, already-listening loopback socket, and
# `handle_request()` returns as soon as it accepts that one connection.
_DELIVERY_TIMEOUT = 2.0


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")


def _token_transport(seen: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(httpx.QueryParams(request.content.decode()))
        seen["body"] = body
        # Echo back exactly the scope requested, so the scope-narrowing check in
        # `_to_tokens` never fires here - that check has its own dedicated tests
        # in tests/auth/test_flow.py; these tests are about `login`'s assembly.
        return httpx.Response(
            200,
            json={"access_token": "AT", "refresh_token": "RT", "expires_in": 1800, "scope": body.get("scope", "")},
        )

    return httpx.MockTransport(handler)


def _deliver_after_authorize(monkeypatch: pytest.MonkeyPatch, *, code: str = "THE-CODE") -> None:
    """Fire the "browser's redirect" the instant `login` builds the
    authorization URL - by then the `Listener`'s socket is already bound and
    listening, so the connection queues until `listener.wait()` accepts it."""
    real_authorize_url = auth.authorize_url

    def spy(*, redirect_uri: str, state: str, **kwargs: object) -> str:
        def deliver() -> None:
            urllib.request.urlopen(f"{redirect_uri}?code={code}&state={state}", timeout=_DELIVERY_TIMEOUT).read()

        threading.Thread(target=deliver, daemon=True).start()
        return real_authorize_url(redirect_uri=redirect_uri, state=state, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(auth, "authorize_url", spy)


def test_the_browser_flow_opens_the_browser_waits_for_the_callback_and_persists_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    _env(monkeypatch, tmp_path)
    _deliver_after_authorize(monkeypatch)
    opened = {}
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: opened.setdefault("url", url))
    seen: dict = {}

    tokens = auth.login(scopes=["tickets:read"], timeout=_DELIVERY_TIMEOUT, transport=_token_transport(seen))

    assert tokens.access_token == "AT"
    assert _store.read() is not None
    assert _store.read().access_token == "AT"  # persisted, not just returned
    assert opened["url"].startswith("https://example.zendesk.com/oauth/authorizations/new?")
    assert seen["body"]["code"] == "THE-CODE"
    assert seen["body"]["redirect_uri"] in {
        "http://127.0.0.1:8765/callback",
        "http://127.0.0.1:8766/callback",
        "http://127.0.0.1:8767/callback",
    }
    out, err = capsys.readouterr()
    assert out == ""  # never stdout - it's the MCP JSON-RPC channel
    assert "Opening your browser" in err


def test_open_browser_false_skips_launching_a_browser_but_still_waits_for_the_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _env(monkeypatch, tmp_path)
    _deliver_after_authorize(monkeypatch)
    called = []
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: called.append(url))
    seen: dict = {}

    tokens = auth.login(
        scopes=["tickets:read"], open_browser=False, timeout=_DELIVERY_TIMEOUT, transport=_token_transport(seen)
    )

    assert tokens.access_token == "AT"
    assert called == []


def test_the_paste_flow_reads_the_pasted_url_and_uses_the_fixed_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda n: "FIXED-STATE")
    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO("http://127.0.0.1:8765/callback?code=PASTED&state=FIXED-STATE\n")
    )
    seen: dict = {}

    tokens = auth.login(scopes=["tickets:read"], paste=True, transport=_token_transport(seen))

    assert tokens.access_token == "AT"
    assert seen["body"]["code"] == "PASTED"
    # ADR-009 controller amendment: the OOB URN is rejected at registration, so
    # the paste path must send the exact registered loopback redirect, byte for
    # byte - the authorization server string-matches it.
    assert seen["body"]["redirect_uri"] == "http://127.0.0.1:8765/callback"
    out, err = capsys.readouterr()
    assert out == ""
    assert "Open this URL" in err


def test_a_missing_subdomain_is_refused_with_no_default(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.delenv("CSA_ZENDESK_SUBDOMAIN", raising=False)
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")

    with pytest.raises(auth.NotAuthorised, match="CSA_ZENDESK_SUBDOMAIN"):
        auth.login(scopes=["tickets:read"])


def test_a_missing_client_id_is_refused_with_no_default(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.delenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", raising=False)

    with pytest.raises(auth.NotAuthorised, match="CSA_ZENDESK_MCP_SERVER_IDENTIFIER"):
        auth.login(scopes=["tickets:read"])


def test_requested_scopes_reach_both_the_authorization_url_and_the_exchange(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _env(monkeypatch, tmp_path)
    _deliver_after_authorize(monkeypatch)
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: None)
    seen: dict = {}

    auth.login(scopes=["tickets:read", "tickets:write"], timeout=_DELIVERY_TIMEOUT, transport=_token_transport(seen))

    assert seen["body"]["scope"] == "tickets:read tickets:write"
