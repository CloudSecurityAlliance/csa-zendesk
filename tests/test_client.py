import pytest

from csa_zendesk import FakeBackend, Policy, PolicyBackend, ZendeskClient
from csa_zendesk import exceptions as exc


def test_the_client_passes_envelopes_through_unshaped():
    c = ZendeskClient(FakeBackend({4: {"id": 4, "subject": "s"}}))
    assert c.get_ticket(ticket_id=4) == {"ticket": {"id": 4, "subject": "s"}}


def test_the_client_reports_the_active_policy_when_wrapped():
    c = ZendeskClient(PolicyBackend(FakeBackend(), Policy.from_profile("default")))
    assert c.policy is not None
    assert "ticket.read" in c.policy.capabilities


def test_the_client_reports_no_policy_for_a_bare_backend():
    # A library embedder may deliberately use an ungated backend. Say so honestly
    # rather than implying a policy exists.
    assert ZendeskClient(FakeBackend()).policy is None


def test_a_backend_whose_policy_attribute_is_not_a_policy_reads_as_unpoliced():
    # Backend is a structural Protocol, so an embedder's class may happen to carry
    # an attribute called `policy` that means something else entirely. Returning it
    # would break `.policy`'s own `Policy | None` annotation and could be mistaken
    # for an active policy. "Unpoliced" is both the safe answer and the true one.
    class BackendWithItsOwnPolicy(FakeBackend):
        policy = "our internal retention policy"

    client = ZendeskClient(BackendWithItsOwnPolicy({1: {"id": 1}}))
    assert client.policy is None


def test_a_policy_refusal_reaches_the_caller_unchanged():
    c = ZendeskClient(PolicyBackend(FakeBackend({1: {"id": 1}}), Policy(frozenset())))
    with pytest.raises(exc.PolicyError):
        c.get_ticket(ticket_id=1)
