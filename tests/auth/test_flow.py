"""Tests for the authorization-code exchange and scope verification.

`test_no_credential_appears_in_an_exchange_failure_message` and
`test_the_failure_message_never_echoes_the_response_body` both guard the same
requirement (no credential in an exception message) from two directions: the
first checks that the credentials THIS PROCESS sent don't come back; the
second checks that arbitrary content Zendesk chose to put in the response body
- which could be anything, including a token, on a real server - doesn't
either. A naive `f"...{response.text}"` implementation would pass the first
(the mock body never contains "SECRET-CODE") and fail the second.
"""

import httpx
import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk.auth import _flow


def _transport(handler):
    return httpx.MockTransport(handler)


def test_the_exchange_posts_the_verifier_and_no_secret():
    seen = {}

    def handler(request):
        seen["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 1800,
                "scope": "tickets:read",
            },
        )

    _flow.exchange_code(
        subdomain="example",
        client_id="cid",
        code="C",
        verifier="V",
        redirect_uri="http://127.0.0.1:1/cb",
        requested_scopes=["tickets:read"],
        transport=_transport(handler),
    )
    assert seen["body"]["code_verifier"] == "V"
    assert seen["body"]["grant_type"] == "authorization_code"
    assert seen["body"]["code"] == "C"
    assert seen["body"]["client_id"] == "cid"
    assert seen["body"]["redirect_uri"] == "http://127.0.0.1:1/cb"
    assert "client_secret" not in seen["body"]


def test_the_exchange_requests_the_maximum_documented_lifetimes():
    # specs/zendesk-support-oas.yaml `CreateTokenForGrantType` (line ~22798):
    # expires_in <= 172,800s (2 days), refresh_token_expires_in <= 7,776,000s
    # (90 days). Both maxima must be requested, not just eligible to be.
    seen = {}

    def handler(request):
        seen["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 1800,
                "scope": "tickets:read",
            },
        )

    _flow.exchange_code(
        subdomain="example",
        client_id="cid",
        code="C",
        verifier="V",
        redirect_uri="http://127.0.0.1:1/cb",
        requested_scopes=["tickets:read"],
        transport=_transport(handler),
    )
    assert seen["body"]["expires_in"] == str(_flow.MAX_ACCESS_TOKEN_LIFETIME_SECONDS)
    assert seen["body"]["refresh_token_expires_in"] == str(_flow.MAX_REFRESH_TOKEN_LIFETIME_SECONDS)


def test_the_maximum_lifetimes_satisfy_the_spec_invariant():
    # The spec requires expires_in <= refresh_token_expires_in. A later edit
    # that inverts the two constants must fail here, not as a 400 at runtime.
    assert _flow.MAX_ACCESS_TOKEN_LIFETIME_SECONDS <= _flow.MAX_REFRESH_TOKEN_LIFETIME_SECONDS


def test_the_maximum_lifetimes_are_the_literal_values_zendesk_documents():
    # specs/zendesk-support-oas.yaml `CreateTokenForGrantType` (~line 22798):
    #   expires_in: >= 300s (5 minutes), <= 172,800s (2 days)
    #   refresh_token_expires_in: >= 604,800s (7 days), <= 7,776,000s (90 days)
    #
    # Bounds are written as literals here, not derived from the constants
    # under test - asserting a constant against itself would only prove the
    # code equals itself. This pins both constants to the numbers Zendesk's
    # own document states, so a future edit that "adjusts" them together to a
    # still-ordered, still-wrong pair (the failure mode the ordering test
    # above cannot see) fails here instead of drifting silently, since these
    # values are transcribed from a vendor document rather than chosen by us.
    assert _flow.MAX_ACCESS_TOKEN_LIFETIME_SECONDS == 172_800
    assert 300 <= _flow.MAX_ACCESS_TOKEN_LIFETIME_SECONDS <= 172_800

    assert _flow.MAX_REFRESH_TOKEN_LIFETIME_SECONDS == 7_776_000
    assert 604_800 <= _flow.MAX_REFRESH_TOKEN_LIFETIME_SECONDS <= 7_776_000


def test_a_granted_scope_narrower_than_requested_is_a_loud_error():
    # API-SURFACE §7: Zendesk issues a token for an unrecognised scope name and
    # then 403s every request made with it, so a typo produces a credential that
    # looks valid and works for nothing. Compare requested against granted.
    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 1800,
                "scope": "tickets:read",
            },
        )

    with pytest.raises(_flow.ScopeError, match="hc:write"):
        _flow.exchange_code(
            subdomain="example",
            client_id="cid",
            code="C",
            verifier="V",
            redirect_uri="http://127.0.0.1:1/cb",
            requested_scopes=["tickets:read", "hc:write"],
            transport=_transport(handler),
        )


def test_a_scope_outside_the_registered_ceiling_is_refused_with_no_token_issued():
    # The OTHER scope failure mode, and it is not silent: a scope outside the
    # client's registered ceiling (`read tickets:write ticket_attachments:write
    # ticket_views:write` on the live client) gets `400 invalid_scope` and no
    # token at all - AuthExchangeError, never ScopeError, because there is no
    # token to compare granted-vs-requested scopes on.
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_scope"})

    with pytest.raises(_flow.AuthExchangeError):
        _flow.exchange_code(
            subdomain="example",
            client_id="cid",
            code="C",
            verifier="V",
            redirect_uri="http://127.0.0.1:1/cb",
            requested_scopes=["hc:write"],
            transport=_transport(handler),
        )


def test_expires_at_is_absolute_not_relative(monkeypatch):
    monkeypatch.setattr(_flow.time, "time", lambda: 1_000.0)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 1800,
                "scope": "tickets:read",
            },
        )

    t = _flow.exchange_code(
        subdomain="example",
        client_id="cid",
        code="C",
        verifier="V",
        redirect_uri="http://127.0.0.1:1/cb",
        requested_scopes=["tickets:read"],
        transport=_transport(handler),
    )
    assert t.expires_at == 2_800.0
    assert t.access_token == "AT"
    assert t.refresh_token == "RT"


def test_no_credential_appears_in_an_exchange_failure_message():
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(_flow.AuthExchangeError) as ei:
        _flow.exchange_code(
            subdomain="example",
            client_id="cid",
            code="SECRET-CODE",
            verifier="SECRET-VERIFIER",
            redirect_uri="http://127.0.0.1:1/cb",
            requested_scopes=["tickets:read"],
            transport=_transport(handler),
        )
    assert "SECRET-CODE" not in str(ei.value)
    assert "SECRET-VERIFIER" not in str(ei.value)


def test_the_failure_message_never_echoes_the_response_body():
    # A server-side error body could, in principle, contain anything - up to
    # and including a token issued moments earlier by a different call. The
    # only thing an exchange failure message may say about the failure is the
    # HTTP status; the response body itself must never reach it.
    def handler(request):
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "token AT-WOULD-LEAK-HERE was rejected"},
        )

    with pytest.raises(_flow.AuthExchangeError) as ei:
        _flow.exchange_code(
            subdomain="example",
            client_id="cid",
            code="C",
            verifier="V",
            redirect_uri="http://127.0.0.1:1/cb",
            requested_scopes=["tickets:read"],
            transport=_transport(handler),
        )
    assert "AT-WOULD-LEAK-HERE" not in str(ei.value)
    assert "400" in str(ei.value)


def test_a_transport_failure_is_translated_not_a_raw_httpx_exception():
    # A network outage (offline, DNS, a proxy, a read timeout) during `auth
    # login` is not a bug, and letting it escape as a raw httpx exception is
    # exactly the hole cli.py's docstring says must not exist.
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(exc.ApiError, match="ConnectError") as ei:
        _flow.exchange_code(
            subdomain="example",
            client_id="cid",
            code="SECRET-CODE",
            verifier="SECRET-VERIFIER",
            redirect_uri="http://127.0.0.1:1/cb",
            requested_scopes=["tickets:read"],
            transport=_transport(handler),
        )
    assert "SECRET-CODE" not in str(ei.value)
    assert "SECRET-VERIFIER" not in str(ei.value)


def test_a_200_response_missing_a_required_field_is_a_typed_error_not_a_bare_keyerror():
    # HTTP 200 with `expires_in` (or any other required field) missing or
    # malformed must still stay inside the ZendeskError hierarchy.
    def handler(request):
        return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "scope": "tickets:read"})

    with pytest.raises(_flow.AuthExchangeError, match="malformed"):
        _flow.exchange_code(
            subdomain="example",
            client_id="cid",
            code="C",
            verifier="V",
            redirect_uri="http://127.0.0.1:1/cb",
            requested_scopes=["tickets:read"],
            transport=_transport(handler),
        )
