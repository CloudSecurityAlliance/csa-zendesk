import httpx
import pytest

from csa_zendesk import _connect as connect_mod
from csa_zendesk import exceptions as exc


def test_connect_returns_a_gated_client(monkeypatch):
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    monkeypatch.setattr(connect_mod.auth, "access_token", lambda: "AT")
    client = connect_mod.connect(capabilities=frozenset({"ticket.read"}))
    assert client.policy is not None
    # Policy's real attribute is `capabilities` (policy.py's __slots__), not
    # `granted` - use the real name rather than inventing a parallel one.
    assert "ticket.read" in client.policy.capabilities


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


def test_a_rejected_token_is_cleared_so_the_retry_cannot_reuse_it(monkeypatch):
    # TODO E12: on_invalid_token must FORCE a new token. access_token() only
    # refreshes inside its 120s margin, so clearing is what makes the retry real.
    monkeypatch.setenv("CSA_ZENDESK_SUBDOMAIN", "example")
    cleared = {"n": 0}
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"ticket": {"id": 1}})

    monkeypatch.setattr(connect_mod.auth, "access_token", lambda: "AT")
    monkeypatch.setattr(connect_mod.auth, "clear", lambda: cleared.__setitem__("n", cleared["n"] + 1))
    client = connect_mod.connect(capabilities=frozenset({"ticket.read"}), transport=httpx.MockTransport(handler))
    client.get_ticket(ticket_id=1)
    assert cleared["n"] == 1
    assert calls["n"] == 2


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
