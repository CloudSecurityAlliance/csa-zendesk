"""Tests for `_flow.revoke` - `DELETE /api/v2/oauth/tokens/current`, the
server-side half of `auth logout` (TODO.md E15).

Three outcomes, each asserted separately because the caller (`auth.logout`)
has to react differently to each:

  - 204/success: nothing raised.
  - 401: `TokenAlreadyInvalid` - the token was already dead, safe to clear
    the local file over.
  - anything else >= 400, or a transport failure: `RevokeError` /
    `exc.ApiError` - the local file must survive this, so every one of those
    tests also asserts no exception attribute or message carries the token.
"""

import httpx
import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk.auth import _flow, _store


def _tokens() -> _store.Tokens:
    return _store.Tokens("AT-SECRET", "RT-SECRET", 9_999.0, "read")


def test_a_successful_revoke_sends_the_bearer_token_and_raises_nothing():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(204)

    _flow.revoke(subdomain="example", tokens=_tokens(), transport=httpx.MockTransport(handler))
    assert seen["method"] == "DELETE"
    assert seen["url"] == "https://example.zendesk.com/api/v2/oauth/tokens/current"
    assert seen["auth"] == "Bearer AT-SECRET"


def test_a_401_means_the_token_was_already_invalid():
    def handler(request):
        return httpx.Response(401)

    with pytest.raises(_flow.TokenAlreadyInvalid):
        _flow.revoke(subdomain="example", tokens=_tokens(), transport=httpx.MockTransport(handler))


def test_a_403_is_a_revoke_error_not_already_invalid():
    # 403 means authenticated-and-refused elsewhere in this codebase
    # (exceptions.PlanBoundary) - it must not be folded into the "already
    # dead, safe to clear" case that 401 is.
    def handler(request):
        return httpx.Response(403)

    with pytest.raises(_flow.RevokeError):
        _flow.revoke(subdomain="example", tokens=_tokens(), transport=httpx.MockTransport(handler))


def test_a_5xx_is_a_revoke_error():
    def handler(request):
        return httpx.Response(503)

    with pytest.raises(_flow.RevokeError, match="503"):
        _flow.revoke(subdomain="example", tokens=_tokens(), transport=httpx.MockTransport(handler))


def test_a_transport_failure_is_translated_not_a_raw_httpx_exception():
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(exc.ApiError, match="ConnectError"):
        _flow.revoke(subdomain="example", tokens=_tokens(), transport=httpx.MockTransport(handler))


def test_no_credential_appears_in_a_revoke_error_message():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(_flow.RevokeError) as ei:
        _flow.revoke(subdomain="example", tokens=_tokens(), transport=httpx.MockTransport(handler))
    assert "AT-SECRET" not in str(ei.value)


def test_no_credential_appears_in_a_token_already_invalid_message():
    def handler(request):
        return httpx.Response(401)

    with pytest.raises(_flow.TokenAlreadyInvalid) as ei:
        _flow.revoke(subdomain="example", tokens=_tokens(), transport=httpx.MockTransport(handler))
    assert "AT-SECRET" not in str(ei.value)
