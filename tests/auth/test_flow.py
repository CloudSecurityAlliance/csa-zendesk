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
