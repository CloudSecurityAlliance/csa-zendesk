"""The point of Block 0: one method, every layer, no network."""

import httpx
import pytest

from csa_zendesk import (
    ApiBackend,
    FakeBackend,
    HttpClient,
    Policy,
    PolicyBackend,
    ZendeskClient,
)
from csa_zendesk import exceptions as exc


def build(handler, policy=None):
    http = HttpClient(
        subdomain="example",
        email="agent@example.com",
        api_token="t",
        transport=httpx.MockTransport(handler),
    )
    if policy is None:
        policy = Policy.from_profile("default")
    return ZendeskClient(PolicyBackend(ApiBackend(http), policy))


def test_http_through_backend_through_policy_through_client():
    def handler(request):
        # ApiBackend.get_ticket deliberately omits the .json suffix (see
        # backend.py's comment: confirmed against the operation inventory and
        # the OpenAPI spec's own response example).
        assert request.url.path == "/api/v2/tickets/12"
        return httpx.Response(200, json={"ticket": {"id": 12, "status": "open"}})

    assert build(handler).get_ticket(ticket_id=12) == {"ticket": {"id": 12, "status": "open"}}


def test_a_403_arrives_as_a_plan_boundary_not_an_outage():
    def handler(request):
        return httpx.Response(403, json={"error": {"title": "Forbidden", "message": "You do not have access"}})

    with pytest.raises(exc.PlanBoundary):
        build(handler).get_ticket(ticket_id=1)


def test_the_two_404s_stay_distinguishable_all_the_way_up():
    def invalid(request):
        return httpx.Response(404, json={"error": "InvalidEndpoint", "description": "Not found"})

    def absent(request):
        return httpx.Response(404, json={"error": "RecordNotFound", "description": "Not found"})

    with pytest.raises(exc.EndpointNotAvailable):
        build(invalid).get_ticket(ticket_id=1)
    with pytest.raises(exc.NotFound):
        build(absent).get_ticket(ticket_id=1)


def test_the_policy_refuses_before_any_http_call_is_made():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"ticket": {}})

    # Every named profile grants ticket.read (`policy.PROFILES`), so a profile
    # name cannot exercise a refusal here - build with an explicit empty
    # policy instead, the same way tests/test_client.py does.
    with pytest.raises(exc.PolicyError):
        build(handler, policy=Policy(frozenset())).get_ticket(ticket_id=1)
    assert calls["n"] == 0, "the refusal must not reach the network"


def test_a_pagination_conflict_is_refused_before_any_request_is_sent():
    # NOTE: this one exercises HttpClient DIRECTLY, not through ZendeskClient, and
    # deliberately so - `get_ticket` takes no pagination parameters, so in Block 0
    # there is no way to express this conflict from the top of the stack. It proves
    # the guard fires before the network, NOT that it is reachable through the
    # client. When a paginating operation lands (list/search in Block 1), this test
    # should move up to go through ZendeskClient like its neighbours.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"tickets": []})

    http = HttpClient(
        subdomain="example",
        email="agent@example.com",
        api_token="t",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(exc.PaginationError):
        http.request("GET", "/api/v2/tickets", params={"page[after]": "abc", "sort_by": "created_at"})
    assert calls["n"] == 0, "mixing pagination styles must never reach the network"


def test_the_fake_backend_path_works_through_the_full_client():
    # The offline tier, through the same ZendeskClient API, no transport at all.
    c = ZendeskClient(PolicyBackend(FakeBackend({7: {"id": 7, "subject": "offline"}}), Policy.from_profile("default")))
    assert c.get_ticket(ticket_id=7) == {"ticket": {"id": 7, "subject": "offline"}}
