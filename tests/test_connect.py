import time

import httpx
import pytest

from csa_zendesk import _connect as connect_mod
from csa_zendesk import exceptions as exc
from csa_zendesk.auth import _store


def test_connect_returns_a_gated_client(monkeypatch):
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setattr(connect_mod.auth, "access_token", lambda: "AT")
    client = connect_mod.connect(capabilities=frozenset({"ticket.read"}))
    assert client.policy is not None
    # Policy's real attribute is `capabilities` (policy.py's __slots__), not
    # `granted` - use the real name rather than inventing a parallel one.
    # Set EQUALITY, not membership (smaller item, final fix wave): matches the
    # profile-path assertion right below (`test_a_named_profile_resolves_to_
    # its_capabilities`), and membership alone would not catch `connect()`
    # silently granting something wider than what was asked for.
    assert client.policy.capabilities == frozenset({"ticket.read"})


def test_the_bearer_comes_from_the_auth_layer(monkeypatch):
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ticket": {"id": 1}})

    monkeypatch.setattr(connect_mod.auth, "access_token", lambda: "AT-FROM-AUTH")
    client = connect_mod.connect(capabilities=frozenset({"ticket.read"}), transport=httpx.MockTransport(handler))
    client.get_ticket(ticket_id=1)
    assert seen["auth"] == "Bearer AT-FROM-AUTH"


def test_a_rejected_token_is_force_refreshed_so_the_retry_carries_a_new_bearer(monkeypatch, tmp_path):
    # Critical 3 (final whole-branch review): `on_invalid_token=auth.clear` used
    # to discard the WHOLE token file - access token and refresh token both,
    # since both live in it - so the retry's `token_provider()` call found
    # nothing on disk and raised `NotAuthorised`; the retry could never
    # succeed. This exercises the REAL `access_token()` (not a monkeypatched
    # stand-in) against an `httpx.MockTransport` that serves both the ticket
    # endpoint and the OAuth token endpoint, and asserts the retry's
    # `Authorization` header actually carries the newly-refreshed bearer - the
    # thing a monkeypatched call-count assertion cannot prove.
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setenv("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "client-id")
    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "tokens.json"))
    # Comfortably outside REFRESH_MARGIN_SECONDS, so a NON-forced access_token()
    # call would hand this back unchanged - proving the forced refresh, not an
    # ordinary proactive one, is what produces the new bearer below.
    _store.write(
        _store.Tokens(access_token="AT-OLD", refresh_token="RT-OLD", expires_at=time.time() + 10_000, scope="read")
    )

    ticket_calls = {"n": 0}
    seen_bearers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/tokens":
            return httpx.Response(
                200,
                json={
                    "access_token": "AT-NEW",
                    "refresh_token": "RT-NEW",
                    "expires_in": 172_800,
                    "refresh_token_expires_in": 7_776_000,
                    "scope": "read",
                },
            )
        ticket_calls["n"] += 1
        seen_bearers.append(request.headers.get("authorization"))
        if ticket_calls["n"] == 1:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"ticket": {"id": 1}})

    client = connect_mod.connect(capabilities=frozenset({"ticket.read"}), transport=httpx.MockTransport(handler))
    client.get_ticket(ticket_id=1)
    assert ticket_calls["n"] == 2  # the original 401 and exactly one retry
    assert seen_bearers == ["Bearer AT-OLD", "Bearer AT-NEW"]
    # The refreshed pair was persisted, not just handed back in memory - the
    # next ordinary (non-forced) access_token() call must see it too.
    assert _store.read().access_token == "AT-NEW"


def test_a_missing_subdomain_is_a_typed_error_not_a_keyerror(monkeypatch):
    monkeypatch.delenv("CSA_ZENDESK_SUBDOMAIN", raising=False)
    with pytest.raises(exc.ZendeskError, match="CSA_ZENDESK_SUBDOMAIN"):
        connect_mod.connect(capabilities=frozenset())


def test_profile_and_capabilities_are_mutually_exclusive(monkeypatch):
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    with pytest.raises(ValueError, match="exactly one"):
        connect_mod.connect(profile="read_only", capabilities=frozenset({"ticket.read"}))


# The five tests above are the brief's spec (with the `granted` -> `capabilities`
# fix noted inline). The two below are added for coverage and for the
# no-default-authority requirement (review finding 2): `connect()` must refuse
# when neither `profile` nor `capabilities` is given, not silently grant some
# policy nobody asked for.


def test_a_named_profile_resolves_to_its_capabilities(monkeypatch):
    from csa_zendesk import policy

    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setattr(connect_mod.auth, "access_token", lambda: "AT")
    client = connect_mod.connect(profile="readonly")
    assert client.policy is not None
    assert client.policy.capabilities == policy.PROFILES["readonly"]


def test_neither_profile_nor_capabilities_is_a_refusal_not_a_default(monkeypatch):
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    with pytest.raises(ValueError, match="exactly one"):
        connect_mod.connect()
