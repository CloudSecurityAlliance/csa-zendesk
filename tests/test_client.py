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


def test_the_client_passes_search_envelopes_through_unshaped():
    c = ZendeskClient(FakeBackend())
    assert c.search_tickets(query="type:ticket status:open") == {"results": [], "count": 0}


def test_a_search_ceiling_refusal_reaches_the_caller_through_the_client():
    # Proves page/per_page actually reach the backend through the client, not
    # just that a canned envelope comes back.
    c = ZendeskClient(FakeBackend())
    with pytest.raises(exc.SearchLimitExceeded, match="1000"):
        c.search_tickets(query="x", page=101, per_page=10)


def test_the_client_passes_comment_envelopes_through_unshaped():
    c = ZendeskClient(FakeBackend({4: {"id": 4}}))
    assert c.list_comments(ticket_id=4) == {"comments": []}


def test_a_list_comments_not_found_reaches_the_caller_through_the_client():
    c = ZendeskClient(FakeBackend())
    with pytest.raises(exc.NotFound):
        c.list_comments(ticket_id=999)


def test_the_client_passes_update_ticket_envelopes_through_unshaped():
    c = ZendeskClient(FakeBackend({4: {"id": 4, "priority": "low"}}))
    assert c.update_ticket(ticket_id=4, fields={"priority": "high"}) == {"ticket": {"id": 4, "priority": "high"}}


def test_an_update_ticket_not_found_reaches_the_caller_through_the_client():
    c = ZendeskClient(FakeBackend())
    with pytest.raises(exc.NotFound):
        c.update_ticket(ticket_id=999, fields={"priority": "high"})


def test_the_client_passes_assign_ticket_envelopes_through_unshaped():
    c = ZendeskClient(FakeBackend({4: {"id": 4}}))
    assert c.assign_ticket(ticket_id=4, assignee_id=7) == {"ticket": {"id": 4, "assignee_id": 7}}


def test_an_assign_ticket_not_found_reaches_the_caller_through_the_client():
    c = ZendeskClient(FakeBackend())
    with pytest.raises(exc.NotFound):
        c.assign_ticket(ticket_id=999, group_id=9)
