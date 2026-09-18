"""Tests for `refresh()` and `access_token()`, the single accessor every
caller uses to get a usable bearer token.

Several properties get their own test beyond what Task 5's brief specified,
because the brief predates them:

  - a refresh response that omits `refresh_token` must not overwrite the
    persisted one with an empty string (Zendesk is not required to rotate it
    every time);
  - a missing `CSA_ZENDESK_SUBDOMAIN` is refused the same way a missing
    `CSA_ZENDESK_MCP_SERVER_IDENTIFIER` is - neither has a default;
  - a refused refresh must not read like a bare 401, because "unauthorised"
    sends an operator looking for a permissions problem when the real cause
    is usually a refresh token that outlived its 30-day default lifetime;
  - a refresh that comes back with FEWER scopes than the credential already
    carried must be refused and never persisted (post-review fix - see the
    module docstring in `_flow.py` and `Tokens.scope` for why `requested_scopes`
    alone, which is empty by default, can never be a usable baseline for this
    check).
"""

import httpx
import pytest

from csa_zendesk.auth import _flow, _store


def _ok(handler_calls):
    def handler(request):
        handler_calls.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200,
            json={
                "access_token": "NEW-AT",
                "refresh_token": "NEW-RT",
                "expires_in": 1800,
                "scope": "tickets:read",
            },
        )

    return httpx.MockTransport(handler)


def test_a_token_inside_the_margin_is_refreshed_before_it_expires(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    # Expires in 60s; the margin is 120s, so this must refresh rather than return it.
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "tickets:read"))
    calls: list[dict] = []
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)
    assert _flow.access_token(transport=_ok(calls)) == "NEW-AT"
    assert calls[0]["grant_type"] == "refresh_token"
    assert "client_secret" not in calls[0]
    assert _store.read().refresh_token == "NEW-RT"  # rotation is persisted


def test_a_healthy_token_is_returned_without_a_request(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    _store.write(_store.Tokens("GOOD-AT", "RT", 9_999.0, "tickets:read"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def explode(request):  # pragma: no cover - must never be reached
        raise AssertionError("refreshed a token that had not expired")

    assert _flow.access_token(transport=httpx.MockTransport(explode)) == "GOOD-AT"


def test_no_token_file_says_how_to_fix_it(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    with pytest.raises(_flow.NotAuthorised, match="csa-zendesk auth login"):
        _flow.access_token()


def test_a_missing_client_id_is_refused_with_no_default(monkeypatch, tmp_path):
    # ADR-009 rejected embedding a CSA client id: every deployment would share one
    # client's scope ceiling and rate-limit attribution.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.delenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", raising=False)
    _store.write(_store.Tokens("AT", "RT", 9_999.0, "read"))
    with pytest.raises(_flow.NotAuthorised, match="CSA_ZENDESK_MCP_SERVER_IDENTIFIER"):
        _flow.access_token()


def test_a_missing_subdomain_is_refused_with_no_default(monkeypatch, tmp_path):
    # Same rule, the other required variable: neither has a default.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.delenv("CSA_ZENDESK_SUBDOMAIN", raising=False)
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    _store.write(_store.Tokens("AT", "RT", 9_999.0, "read"))
    with pytest.raises(_flow.NotAuthorised, match="CSA_ZENDESK_SUBDOMAIN"):
        _flow.access_token()


def test_a_refresh_response_without_a_new_refresh_token_keeps_the_old_one(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    # Scope unchanged ("read" -> "read") so this test isolates the
    # refresh_token property from the separate scope-narrowing check below.
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "read"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def handler(request):
        # No refresh_token in the response at all - Zendesk is not required to
        # rotate it on every refresh.
        return httpx.Response(200, json={"access_token": "NEW-AT", "expires_in": 1800, "scope": "read"})

    assert _flow.access_token(transport=httpx.MockTransport(handler)) == "NEW-AT"
    assert _store.read().refresh_token == "OLD-RT"  # kept, never overwritten with ""


def test_csa_zendesk_scopes_is_sent_and_checked_when_set(monkeypatch, tmp_path):
    # The default (unset CSA_ZENDESK_SCOPES) sends no `scope` at all, exercised
    # by the tests above. This exercises the other branch: an operator who set
    # it gets that scope requested on the wire and validated against what came
    # back, same as `exchange_code`.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    monkeypatch.setenv("CSA_ZENDESK_SCOPES", "tickets:read")
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "tickets:read"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)
    calls: list[dict] = []
    assert _flow.access_token(transport=_ok(calls)) == "NEW-AT"
    assert calls[0]["scope"] == "tickets:read"


def test_a_refused_refresh_names_both_causes_not_a_bare_401(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "read"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(_flow.NotAuthorised, match="auth login") as ei:
        _flow.access_token(transport=httpx.MockTransport(handler))
    message = str(ei.value)
    assert "expired" in message
    assert "refused" in message
    assert "OLD-RT" not in message
    assert isinstance(ei.value.__cause__, _flow.AuthExchangeError)


def test_a_refresh_that_narrows_scope_is_refused_and_not_persisted(monkeypatch, tmp_path):
    # CRITICAL from the Task 5 review: with `requested_scopes` empty by default,
    # `set() - granted` is always empty, so a scope check against `requested`
    # can never fire on the default path. This credential was issued with
    # `read tickets:write`; the client's registered ceiling has since narrowed
    # to `read` (e.g. an admin tightened it), so a refresh grants only `read`.
    # That loss must be caught against the STORED grant, not the request.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "read tickets:write"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": "NARROWED-AT",
                "refresh_token": "NARROWED-RT",
                "expires_in": 1800,
                "scope": "read",
            },
        )

    with pytest.raises(_flow.ScopeError, match="tickets:write"):
        _flow.access_token(transport=httpx.MockTransport(handler))

    # A check that raises but has already written is not a check: the narrowed
    # token must never reach disk, and the old, wider grant must still be there.
    stored = _store.read()
    assert stored.access_token == "OLD-AT"
    assert stored.scope == "read tickets:write"


def test_a_refresh_granting_a_superset_in_a_different_order_does_not_raise(monkeypatch, tmp_path):
    # The comparison is a set difference, so neither a superset nor scope
    # ordering may trip the check.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "tickets:write read"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": "NEW-AT",
                "refresh_token": "NEW-RT",
                "expires_in": 1800,
                "scope": "read hc:read tickets:write",
            },
        )

    assert _flow.access_token(transport=httpx.MockTransport(handler)) == "NEW-AT"
    assert _store.read().scope == "read hc:read tickets:write"
