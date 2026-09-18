"""Tests for `whoami` and the anonymous-user trap it exists to catch.

Every test pre-seeds a fresh, non-expiring token via `_store.write` rather
than exercising `login` - `whoami` calls `access_token()` internally with no
`transport` of its own, so a token that still had to be refreshed would send
a REAL network request, which the "no network in tests" rule forbids. A fresh
cached token keeps `access_token()` on its file-read-and-compare fast path,
so the only HTTP the test ever sees is the `users/me.json` request against
the `transport` the test passes to `whoami` directly.

Imported via `csa_zendesk.auth` (the public package), not
`csa_zendesk.auth.whoami` (the module): `auth/__init__.py` does
`from .whoami import whoami`, which rebinds the `auth` package's `whoami`
attribute to the *function* - so `from csa_zendesk.auth import whoami as w`
silently gets the function, not the module, and `auth.NotAuthenticated` would
fail with `AttributeError`. Going through the public package the way a real
caller would sidesteps the ambiguity entirely.
"""

import time

import httpx
import pytest

from csa_zendesk import auth
from csa_zendesk.auth import _store


def _authorised(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    _store.write(
        _store.Tokens(access_token="AT", refresh_token="RT", expires_at=time.time() + 9_999, scope="tickets:read")
    )


def test_an_anonymous_user_object_is_treated_as_unauthenticated(monkeypatch, tmp_path):
    # CLAUDE.md / API-SURFACE.md §5.1 invariant: users/me.json answers HTTP 200
    # with an "Anonymous user" object when wholly unauthenticated. A naive
    # whoami reports success for a credential that authenticates nothing.
    _authorised(monkeypatch, tmp_path)

    def handler(request):
        return httpx.Response(200, json={"user": {"id": None, "name": "Anonymous user", "role": "end-user"}})

    with pytest.raises(auth.NotAuthenticated, match="Anonymous"):
        auth.whoami(transport=httpx.MockTransport(handler))


def test_a_real_identity_is_returned(monkeypatch, tmp_path):
    _authorised(monkeypatch, tmp_path)

    def handler(request):
        return httpx.Response(200, json={"user": {"id": 42, "name": "Agent", "role": "admin"}})

    who = auth.whoami(transport=httpx.MockTransport(handler))
    assert who["id"] == 42 and who["role"] == "admin"


def test_the_bearer_token_is_sent(monkeypatch, tmp_path):
    _authorised(monkeypatch, tmp_path)
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"user": {"id": 1, "name": "Agent"}})

    auth.whoami(transport=httpx.MockTransport(handler))
    assert seen["authorization"] == "Bearer AT"


def test_subdomain_defaults_to_the_environment_variable(monkeypatch, tmp_path):
    _authorised(monkeypatch, tmp_path)
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        return httpx.Response(200, json={"user": {"id": 1, "name": "Agent"}})

    auth.whoami(transport=httpx.MockTransport(handler))
    assert seen["host"] == "example.zendesk.com"


def test_an_explicit_subdomain_overrides_the_environment_variable(monkeypatch, tmp_path):
    _authorised(monkeypatch, tmp_path)
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        return httpx.Response(200, json={"user": {"id": 1, "name": "Agent"}})

    auth.whoami(subdomain="acme", transport=httpx.MockTransport(handler))
    assert seen["host"] == "acme.zendesk.com"


def test_a_response_with_no_user_object_at_all_is_treated_as_unauthenticated(monkeypatch, tmp_path):
    # Defensive: some non-200-shaped or unexpected body should not be read as
    # a real identity just because it parses as JSON.
    _authorised(monkeypatch, tmp_path)

    def handler(request):
        return httpx.Response(200, json={})

    with pytest.raises(auth.NotAuthenticated):
        auth.whoami(transport=httpx.MockTransport(handler))
