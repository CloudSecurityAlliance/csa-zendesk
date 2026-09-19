"""Tests for `auth.logout`, the orchestration `cli.py`'s `auth logout`
subcommand calls: read the stored token, revoke it server-side, then clear
the local file - in that order, and only clear on an outcome that says it is
safe to (success, or the token already being dead).
"""

import httpx
import pytest

from csa_zendesk import auth
from csa_zendesk.auth import _store


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")


def test_no_token_file_returns_no_token_and_touches_nothing(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)

    def explode(request):  # pragma: no cover - must never be reached
        raise AssertionError("revoked with no token on disk")

    assert auth.logout(transport=httpx.MockTransport(explode)) == "no-token"


def test_a_successful_revoke_clears_the_file_and_returns_revoked(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    _store.write(_store.Tokens("AT-SECRET", "RT-SECRET", 9_999.0, "read"))

    def handler(request):
        return httpx.Response(204)

    assert auth.logout(transport=httpx.MockTransport(handler)) == "revoked"
    assert _store.read() is None


def test_an_already_invalid_token_is_cleared_anyway_and_reported_distinctly(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    _store.write(_store.Tokens("AT-SECRET", "RT-SECRET", 9_999.0, "read"))

    def handler(request):
        return httpx.Response(401)

    assert auth.logout(transport=httpx.MockTransport(handler)) == "already-invalid"
    assert _store.read() is None  # a dead credential leaves nothing to protect


def test_a_genuine_revoke_failure_leaves_the_file_in_place(monkeypatch, tmp_path):
    # The order-of-operations invariant: revoke first, clear second - and if
    # revoke fails for a reason other than "already dead", never clear at
    # all. Otherwise a network blip would delete the one credential that
    # could still be used to revoke itself later.
    _env(monkeypatch, tmp_path)
    _store.write(_store.Tokens("AT-SECRET", "RT-SECRET", 9_999.0, "read"))

    def handler(request):
        return httpx.Response(503)

    with pytest.raises(auth.RevokeError):
        auth.logout(transport=httpx.MockTransport(handler))
    assert _store.read() is not None


def test_a_transport_failure_leaves_the_file_in_place(monkeypatch, tmp_path):
    from csa_zendesk import exceptions as exc

    _env(monkeypatch, tmp_path)
    _store.write(_store.Tokens("AT-SECRET", "RT-SECRET", 9_999.0, "read"))

    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(exc.ApiError):
        auth.logout(transport=httpx.MockTransport(handler))
    assert _store.read() is not None


def test_a_missing_subdomain_is_refused_before_touching_the_network(monkeypatch, tmp_path):
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    monkeypatch.delenv("CSA_ZENDESK_SUBDOMAIN", raising=False)
    _store.write(_store.Tokens("AT-SECRET", "RT-SECRET", 9_999.0, "read"))

    def explode(request):  # pragma: no cover - must never be reached
        raise AssertionError("reached the network with no subdomain configured")

    with pytest.raises(auth.NotAuthorised, match="CSA_ZENDESK_SUBDOMAIN"):
        auth.logout(transport=httpx.MockTransport(explode))
    assert _store.read() is not None
