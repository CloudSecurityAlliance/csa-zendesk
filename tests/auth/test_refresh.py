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
    module docstring in `_flow.py` and `Tokens.scope` for why the baseline
    must be what this credential already carried, never what is being
    requested this time);
  - `CSA_ZENDESK_SCOPES` must never reach a refresh request at all (final
    review fix): it is `login()`'s knob for the one browser consent screen a
    human sees, and reading it here put an operator's mutable environment
    variable on the wire on every refresh, in both directions at once - widen
    it after login and a refresh would ask for, and (since the check only
    looks for *missing* scopes) accept and persist, more than the browser
    ever granted; narrow it for an unrelated reason and Zendesk would grant
    exactly that narrower request, which the check would then misreport as
    the registered scope ceiling narrowing since issuance - true accusation,
    wrong cause. `refresh()` no longer takes a `requested_scopes` parameter at
    all: it sends no `scope` on the wire, ever, letting Zendesk keep whatever
    this credential already carries.
"""

import httpx
import pytest

from csa_zendesk import exceptions as exc
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


def test_refresh_requests_the_maximum_documented_lifetimes(monkeypatch, tmp_path):
    # Resending the maxima on every refresh matters because rotation mints a
    # brand-new refresh token each time - without re-requesting 90 days here,
    # the window would fall back to Zendesk's 30-day default on the very
    # first refresh instead of sliding forward.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "tickets:read"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)
    calls: list[dict] = []
    assert _flow.access_token(transport=_ok(calls)) == "NEW-AT"
    assert calls[0]["expires_in"] == str(_flow.MAX_ACCESS_TOKEN_LIFETIME_SECONDS)
    assert calls[0]["refresh_token_expires_in"] == str(_flow.MAX_REFRESH_TOKEN_LIFETIME_SECONDS)


def test_refresh_never_sends_a_scope_parameter(monkeypatch, tmp_path):
    # The default (unset CSA_ZENDESK_SCOPES) sends no `scope` at all - this
    # confirms that is not incidental to the env var being unset, but
    # unconditional: refresh() takes no requested_scopes parameter any more.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    monkeypatch.delenv("CSA_ZENDESK_SCOPES", raising=False)
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "tickets:read"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)
    calls: list[dict] = []
    assert _flow.access_token(transport=_ok(calls)) == "NEW-AT"
    assert "scope" not in calls[0]


def test_a_widened_csa_zendesk_scopes_env_var_does_not_widen_a_refresh(monkeypatch, tmp_path):
    # Regression (final review, Task 5 was insufficient): access_token() used
    # to read CSA_ZENDESK_SCOPES and pass it to refresh() as requested_scopes,
    # which put it on the wire. An operator logs in with the default scope
    # "read", then later widens the env var - e.g. to enable a new tool that
    # needs "tickets:write" - with no new consent step. Because the scope
    # check only looks for scopes MISSING from the grant, a superset granted
    # on refresh would pass and be persisted: the credential gains write
    # authority no browser consent ever produced. RFC 6749 §6 forbids a
    # refresh from requesting scope beyond the original grant; not sending
    # scope at all is what makes that impossible rather than merely unlikely.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    monkeypatch.setenv("CSA_ZENDESK_SCOPES", "read tickets:write")  # wider than the stored grant
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "read"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def handler(request):
        params = dict(httpx.QueryParams(request.content.decode()))
        assert "scope" not in params  # the wider env var must never reach the wire
        return httpx.Response(
            200,
            json={"access_token": "NEW-AT", "refresh_token": "NEW-RT", "expires_in": 1800, "scope": "read"},
        )

    assert _flow.access_token(transport=httpx.MockTransport(handler)) == "NEW-AT"
    assert _store.read().scope == "read"  # never widened to include tickets:write


def test_a_narrowed_csa_zendesk_scopes_env_var_does_not_cause_a_false_scope_error(monkeypatch, tmp_path):
    # Regression, the opposite direction: an operator sets CSA_ZENDESK_SCOPES
    # to something narrower than the stored grant, for a reason that has
    # nothing to do with this credential (e.g. preparing a future login). The
    # old code would send that narrower value as the refresh's requested
    # scope; if Zendesk granted exactly what was requested, the check would
    # compare it against the wider stored baseline and raise ScopeError,
    # blaming "the client's registered scope ceiling narrowed" - which would
    # be false, since the operator's own env var caused it, not Zendesk. With
    # no scope ever sent, this env var cannot affect what refresh asks for.
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "cid")
    monkeypatch.setenv("CSA_ZENDESK_SCOPES", "tickets:read")  # narrower, and irrelevant to this grant
    _store.write(_store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "read"))
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def handler(request):
        params = dict(httpx.QueryParams(request.content.decode()))
        assert "scope" not in params
        # Zendesk echoes back the full existing grant, since nothing narrower
        # was ever requested on the wire.
        return httpx.Response(
            200,
            json={"access_token": "NEW-AT", "refresh_token": "NEW-RT", "expires_in": 1800, "scope": "read"},
        )

    assert _flow.access_token(transport=httpx.MockTransport(handler)) == "NEW-AT"
    assert _store.read().scope == "read"


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


def test_a_transport_failure_during_refresh_is_translated_not_a_raw_httpx_exception():
    # A network failure inside refresh() must stay typed (exc.ApiError, naming
    # the token endpoint), not surface as a raw httpx exception, and not be
    # miscategorised as NotAuthorised ("run auth login again") - a transient
    # outage is not the same problem as a dead refresh token.
    tokens = _store.Tokens("OLD-AT", "OLD-RT", 1_060.0, "read")

    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(exc.ApiError, match="ConnectError") as ei:
        _flow.refresh(subdomain="example", client_id="cid", tokens=tokens, transport=httpx.MockTransport(handler))
    assert "OLD-RT" not in str(ei.value)
