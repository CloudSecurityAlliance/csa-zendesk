import inspect
import json
from typing import Any

import httpx
import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk._http import HttpClient
from csa_zendesk.backend import ApiBackend, Backend, FakeBackend, NothingToAssign


def _client(handler) -> HttpClient:
    """A throwaway HttpClient over a MockTransport - no network, ever."""
    return HttpClient(subdomain="example", token_provider=lambda: "tok", transport=httpx.MockTransport(handler))


def _public_methods(cls: Any) -> set[str]:
    """Public callables declared on a class, Protocol machinery and dunders excluded."""
    return {n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n, None))}


def test_fake_backend_satisfies_the_protocol():
    assert isinstance(FakeBackend(), Backend)


def test_api_backend_satisfies_the_protocol():
    http = HttpClient(
        subdomain="example",
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    assert isinstance(ApiBackend(http), Backend)


def test_isinstance_check_proves_method_names_only_not_signatures():
    # runtime_checkable Protocol isinstance only checks that the named methods
    # exist - it does not check parameter names, kinds, or types. The dedicated
    # signature-equality test below is what actually proves the two backends
    # agree on how they are called.
    class NameOnlyImpostor:
        def get_ticket(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

        def search_tickets(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

        def list_comments(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

        def update_ticket(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

        def assign_ticket(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

    assert isinstance(NameOnlyImpostor(), Backend)


def test_the_protocol_and_both_backends_declare_exactly_the_same_methods():
    # The union, not the Protocol's own members: a method added to ApiBackend and not
    # declared on the Protocol is invisible to a guard that only walks the Protocol,
    # which is the half that cannot drift. FakeBackend powers every unit test, so a
    # method it lacks leaves the suite exercising a stale double and passing.
    protocol, real, fake = _public_methods(Backend), _public_methods(ApiBackend), _public_methods(FakeBackend)
    assert protocol == real == fake, (
        f"backend drift - only on Protocol: {sorted(protocol - real - fake)}; "
        f"only on ApiBackend: {sorted(real - protocol - fake)}; "
        f"only on FakeBackend: {sorted(fake - protocol - real)}"
    )


def test_the_two_backends_have_identical_signatures():
    names = _public_methods(Backend) | _public_methods(ApiBackend) | _public_methods(FakeBackend)
    assert names, "signature guard has gone vacuous - no public backend methods found"
    for name in sorted(names):
        assert hasattr(FakeBackend, name), f"{name} is missing from FakeBackend"
        assert hasattr(ApiBackend, name), f"{name} is missing from ApiBackend"
        fake = inspect.signature(getattr(FakeBackend, name))
        real = inspect.signature(getattr(ApiBackend, name))
        assert fake == real, f"{name}: fake {fake} != real {real}"


def test_every_backend_method_takes_keyword_only_arguments():
    # PolicyBackend wraps uniformly; a positional argument would break that.
    names = _public_methods(Backend) | _public_methods(ApiBackend) | _public_methods(FakeBackend)
    assert names, "keyword-only guard has gone vacuous - no public backend methods found"
    for name in sorted(names):
        for cls in (ApiBackend, FakeBackend):
            if not hasattr(cls, name):
                continue  # the set-equality test above is what reports a missing method
            for pname, p in inspect.signature(getattr(cls, name)).parameters.items():
                if pname == "self":
                    continue
                assert p.kind is inspect.Parameter.KEYWORD_ONLY, f"{cls.__name__}.{name}.{pname}"


def test_get_ticket_returns_the_raw_envelope():
    fake = FakeBackend(tickets={7: {"id": 7, "subject": "hello", "status": "open"}})
    env = fake.get_ticket(ticket_id=7)
    # RAW: the upstream envelope, not a model and not the inner object.
    assert env == {"ticket": {"id": 7, "subject": "hello", "status": "open"}}


def test_fake_backend_does_not_leak_its_stored_dict_by_reference():
    # A caller mutating the returned envelope must never corrupt the fixture
    # backing store - the fake copies, it does not share. This is the
    # TOP-LEVEL case: a plain dict() copy already protects it, which is exactly
    # why the nested case below needs its own test.
    tickets = {7: {"id": 7, "subject": "hello"}}
    fake = FakeBackend(tickets=tickets)
    env = fake.get_ticket(ticket_id=7)
    env["ticket"]["subject"] = "tampered"
    assert fake.get_ticket(ticket_id=7)["ticket"]["subject"] == "hello"


def test_fake_backend_does_not_leak_a_nested_field_by_reference():
    # A shallow dict(...) copies only the top level, so mutating a NESTED field
    # (satisfaction_rating.comment here - real envelopes nest nearly everywhere:
    # via, satisfaction_rating, custom_fields, fields) through a returned
    # envelope would otherwise corrupt both the fixture's backing store and the
    # dict the caller originally passed to the constructor. copy.deepcopy is
    # what closes that.
    original = {"id": 7, "subject": "hello", "satisfaction_rating": {"score": "good", "comment": "great support"}}
    constructor_arg = {7: original}
    fake = FakeBackend(tickets=constructor_arg)

    first = fake.get_ticket(ticket_id=7)
    first["ticket"]["satisfaction_rating"]["comment"] = "tampered"

    second = fake.get_ticket(ticket_id=7)
    # 1. the backing store is unaffected - a second call sees the original value.
    assert second["ticket"]["satisfaction_rating"]["comment"] == "great support"
    # 2. the caller's own constructor dict is unaffected too.
    assert original["satisfaction_rating"]["comment"] == "great support"
    assert constructor_arg[7]["satisfaction_rating"]["comment"] == "great support"
    # 3. equal but not the same object, at the nested level as well as the top.
    assert first == {"ticket": {**original, "satisfaction_rating": {"score": "good", "comment": "tampered"}}}
    assert second is not first
    assert second["ticket"] is not first["ticket"]
    assert second["ticket"]["satisfaction_rating"] is not first["ticket"]["satisfaction_rating"]


def test_fake_backend_raises_the_same_error_type_as_the_real_one_for_a_missing_id():
    with pytest.raises(exc.NotFound):
        FakeBackend().get_ticket(ticket_id=999)


def test_api_backend_calls_the_documented_path():
    # Path from analysis/operation-inventory.csv row: ticketing,Tickets,GET,
    # /api/v2/tickets/{ticket_id},ShowTicket,Show Ticket,,,yes - confirmed
    # against specs/zendesk-support-oas.yaml. No .json suffix: that is a quirk
    # of the unrelated Countries family (the only rows in the 883-row inventory
    # that carry one), not a convention ShowTicket follows.
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ticket": {"id": 7}})

    http = HttpClient(subdomain="example", token_provider=lambda: "tok", transport=httpx.MockTransport(handler))
    assert ApiBackend(http).get_ticket(ticket_id=7) == {"ticket": {"id": 7}}
    assert seen["path"] == "/api/v2/tickets/7"


def test_api_backend_returns_the_envelope_unshaped():
    # The seam must not normalise, rename, prune, or otherwise "tidy" the
    # upstream body - whatever Zendesk sends back is exactly what comes out.
    body = {"ticket": {"id": 7, "subject": "hello", "custom_fields": [{"id": 1, "value": None}]}}
    http = HttpClient(
        subdomain="example",
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )
    assert ApiBackend(http).get_ticket(ticket_id=7) == body


def test_fake_and_api_backend_are_mutually_consistent_on_envelope_shape():
    # NOT external corroboration - both sides were written by the same author to
    # agree, so this can only prove FakeBackend and ApiBackend are mutually
    # consistent with each other, not that either matches the live API. That
    # still matters: it is what keeps the fake usable as every unit test's
    # foundation, so one drifting would poison every test built on it.
    #
    # The actual external evidence for this shape is specs/zendesk-support-oas.yaml,
    # operationId ShowTicket (line 15773), whose own inline "200" response
    # example is a "ticket" envelope with fields including "satisfaction_rating"
    # nested under it (lines 15835-15836: satisfaction_rating.comment).
    ticket = {"id": 7, "subject": "hello", "status": "open"}

    fake_env = FakeBackend(tickets={7: ticket}).get_ticket(ticket_id=7)

    http = HttpClient(
        subdomain="example",
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ticket": ticket})),
    )
    api_env = ApiBackend(http).get_ticket(ticket_id=7)

    assert fake_env == api_env
    assert set(fake_env) == {"ticket"}  # both are the raw envelope, not the inner object


# --- search_tickets: offset paging only, refused past the 1000-result ceiling -


def test_search_composes_type_ticket_onto_the_query():
    # Important 5 (final whole-branch review): unconstrained, this endpoint
    # answers type:user/type:organization too - PEOPLE_READ territory this
    # tool's E1_CAPABILITIES never grants. The fix COMPOSES the constraint
    # rather than inspecting the caller's query for an existing `type:` -
    # this asserts what actually reaches the wire, not just that the fix
    # "exists" as a docstring claim.
    seen = {}

    def handler(request):
        seen["query"] = request.url.params["query"]
        return httpx.Response(200, json={"results": [], "count": 0})

    ApiBackend(_client(handler)).search_tickets(query="status:open")
    assert "type:ticket" in seen["query"]
    assert "status:open" in seen["query"]


def test_search_composing_type_ticket_cannot_be_widened_by_a_callers_own_type():
    # The property that matters, not just the mechanism: a caller who tries to
    # widen the result set to users by supplying their own `type:` cannot -
    # Zendesk's search grammar ANDs repeated occurrences of a single-valued
    # field, so the composed query can only ever narrow, never broaden, what
    # this tool returns. This does not send a live request against Zendesk
    # (CLAUDE.md); it pins the request THIS client builds.
    seen = {}

    def handler(request):
        seen["query"] = request.url.params["query"]
        return httpx.Response(200, json={"results": [], "count": 0})

    ApiBackend(_client(handler)).search_tickets(query="type:user")
    assert seen["query"].count("type:") == 2
    assert "type:ticket" in seen["query"]


def test_search_sends_offset_paging_and_the_query():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"results": [], "count": 0})

    b = ApiBackend(_client(handler))
    b.search_tickets(query="type:ticket status:open", page=2, per_page=25)
    assert "per_page=25" in seen["url"]
    assert "page=2" in seen["url"]
    assert "page%5Bsize%5D" not in seen["url"]  # cursor paging is a 400 here


def test_a_request_past_the_thousand_result_ceiling_is_refused_before_the_call():
    called = {"n": 0}

    def handler(request):  # pragma: no cover - must never run
        called["n"] += 1
        return httpx.Response(200, json={})

    b = ApiBackend(_client(handler))
    with pytest.raises(exc.ZendeskError, match="1000"):
        b.search_tickets(query="x", page=101, per_page=10)
    assert called["n"] == 0


def test_the_ceiling_error_names_the_uncapped_alternative():
    def handler(request):  # pragma: no cover - must never run
        return httpx.Response(200, json={})

    b = ApiBackend(_client(handler))
    with pytest.raises(exc.ZendeskError, match="search/export"):
        b.search_tickets(query="x", page=101, per_page=10)


def test_the_last_retrievable_page_is_allowed():
    def handler(request):
        return httpx.Response(200, json={"results": [], "count": 999999})

    b = ApiBackend(_client(handler))
    assert b.search_tickets(query="x", page=100, per_page=10) == {"results": [], "count": 999999}


def test_the_raw_envelope_is_returned_unshaped():
    # ADR-002: the backend never maps, renames or prunes.
    body = {"results": [{"id": 1}], "count": 999999, "facets": None, "next_page": None}

    def handler(request):
        return httpx.Response(200, json=body)

    assert ApiBackend(_client(handler)).search_tickets(query="x") == body


def test_search_uses_the_documented_path():
    # Path from analysis/operation-inventory.csv row: ticketing,Search,GET,
    # /api/v2/search,ListSearchResults,List Search Results,,,yes - confirmed
    # against specs/zendesk-support-oas.yaml (operationId ListSearchResults).
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"results": [], "count": 0})

    b = ApiBackend(_client(handler))
    b.search_tickets(query="x")
    assert seen["path"] == "/api/v2/search"


def test_fake_backend_returns_a_canned_search_envelope():
    assert FakeBackend().search_tickets(query="type:ticket") == {"results": [], "count": 0}


def test_fake_backend_also_refuses_past_the_search_ceiling():
    # If the fake did not enforce this, a test written against it would pass a
    # call the real API rejects outright with HTTP 422.
    with pytest.raises(exc.ZendeskError, match="1000"):
        FakeBackend().search_tickets(query="x", page=101, per_page=10)


# --- list_comments: a ticket without its comments is just a subject line -----


def test_list_comments_returns_the_raw_envelope():
    body = {
        "comments": [
            {"id": 1, "public": True, "body": "hello", "author_id": 7},
            {"id": 2, "public": False, "body": "internal", "author_id": 8},
        ]
    }

    def handler(request):
        return httpx.Response(200, json=body)

    assert ApiBackend(_client(handler)).list_comments(ticket_id=42) == body


def test_list_comments_preserves_the_public_flag_per_comment():
    # API-SURFACE §5.4f: comment.public has no fixed default - it inherits from
    # the ticket's first comment. Flattening it would hide whether a message
    # reached the customer.
    body = {"comments": [{"id": 1, "public": True}, {"id": 2, "public": False}]}

    def handler(request):
        return httpx.Response(200, json=body)

    got = ApiBackend(_client(handler)).list_comments(ticket_id=42)
    assert [c["public"] for c in got["comments"]] == [True, False]


def test_list_comments_uses_the_documented_path():
    # Path from analysis/operation-inventory.csv row: ticketing,Ticket Comments,
    # GET,/api/v2/tickets/{ticket_id}/comments,ListTicketComments,List Comments,
    # cursor,,yes - confirmed against specs/zendesk-support-oas.yaml (operationId
    # ListTicketComments). No .json suffix, matching get_ticket.
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"comments": []})

    b = ApiBackend(_client(handler))
    b.list_comments(ticket_id=42)
    assert seen["path"] == "/api/v2/tickets/42/comments"


def test_fake_backend_returns_a_canned_comments_envelope():
    assert FakeBackend(tickets={7: {"id": 7}}).list_comments(ticket_id=7) == {"comments": []}


def test_fake_backend_raises_not_found_for_an_unknown_ticket_id():
    # Mirrors get_ticket: ticket_id is this call's actual subject (unlike
    # search's free-text query), so an id nothing has ever heard of should not
    # silently read as "a ticket with zero comments".
    with pytest.raises(exc.NotFound):
        FakeBackend().list_comments(ticket_id=999)


# --- update_ticket: PUT /tickets/{id}, constrained to a field edit at the seam


def test_api_backend_update_ticket_uses_the_documented_path_and_wraps_the_body():
    # Path and operation from analysis/operation-inventory.csv row: ticketing,
    # Tickets,PUT,/api/v2/tickets/{ticket_id},UpdateTicket,Update Ticket,,,yes
    # - no .json suffix, matching get_ticket. Confirmed against
    # specs/zendesk-support-oas.yaml (operationId UpdateTicket).
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ticket": {"id": 7, "priority": "high"}})

    b = ApiBackend(_client(handler))
    result = b.update_ticket(ticket_id=7, fields={"priority": "high"})
    assert seen["method"] == "PUT"
    assert seen["path"] == "/api/v2/tickets/7"
    assert seen["body"] == {"ticket": {"priority": "high"}}
    assert result == {"ticket": {"id": 7, "priority": "high"}}


def test_api_backend_update_ticket_returns_the_envelope_unshaped():
    # ADR-002: no mapping, renaming or pruning.
    body = {"ticket": {"id": 7, "priority": "high", "custom_fields": [{"id": 1, "value": None}]}}

    def handler(request):
        return httpx.Response(200, json=body)

    assert ApiBackend(_client(handler)).update_ticket(ticket_id=7, fields={"priority": "high"}) == body


def test_fake_backend_update_ticket_mutates_and_returns_the_ticket():
    fake = FakeBackend(tickets={7: {"id": 7, "priority": "low"}})
    result = fake.update_ticket(ticket_id=7, fields={"priority": "high"})
    assert result == {"ticket": {"id": 7, "priority": "high"}}
    # The mutation is visible on a subsequent read, the same as the real API.
    assert fake.get_ticket(ticket_id=7)["ticket"]["priority"] == "high"


def test_fake_backend_update_ticket_does_not_leak_the_caller_fields_dict_by_reference():
    fake = FakeBackend(tickets={7: {"id": 7}})
    fields = {"custom_fields": [{"id": 1, "value": "x"}]}
    fake.update_ticket(ticket_id=7, fields=fields)
    fields["custom_fields"][0]["value"] = "tampered"
    assert fake.get_ticket(ticket_id=7)["ticket"]["custom_fields"] == [{"id": 1, "value": "x"}]


def test_fake_backend_update_ticket_raises_not_found_for_an_unknown_ticket_id():
    with pytest.raises(exc.NotFound):
        FakeBackend().update_ticket(ticket_id=999, fields={"priority": "high"})


# --- assign_ticket: same PUT, bucket-pure by allowlist rather than by denylist


def test_api_backend_assign_ticket_sends_only_the_provided_fields():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ticket": {"id": 7, "assignee_id": 42}})

    b = ApiBackend(_client(handler))
    b.assign_ticket(ticket_id=7, assignee_id=42)
    assert seen["body"] == {"ticket": {"assignee_id": 42}}


def test_api_backend_assign_ticket_sends_both_fields_when_both_are_given():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ticket": {"id": 7}})

    b = ApiBackend(_client(handler))
    b.assign_ticket(ticket_id=7, assignee_id=42, group_id=9)
    assert seen["body"] == {"ticket": {"assignee_id": 42, "group_id": 9}}


def test_api_backend_assign_ticket_refuses_an_empty_assignment_before_the_call():
    # A caller reaching Backend directly (ADR-002's public seam) never passes
    # through tools.TOOLS["assign_ticket"]'s _only(...) check at all, and even
    # a caller who does isn't stopped by it - _only permits any subset of its
    # allowed keys, including the empty one. An empty-body PUT would still be
    # a real write: it spends rate-limit budget and lands in the ticket's
    # audit log as an update that changed nothing. Refused here, before the
    # request is ever built, matching test_a_request_past_the_thousand_
    # result_ceiling_is_refused_before_the_call's shape.
    called = {"n": 0}

    def handler(request):  # pragma: no cover - must never run
        called["n"] += 1
        return httpx.Response(200, json={})

    b = ApiBackend(_client(handler))
    with pytest.raises(NothingToAssign, match="assignee_id"):
        b.assign_ticket(ticket_id=7)
    assert called["n"] == 0


def test_fake_backend_assign_ticket_refuses_an_empty_assignment_too():
    # Shares _refuse_an_empty_assignment with ApiBackend, the same way
    # search's ceiling check is shared - a fake that let this through would
    # pass a call the real backend rejects outright.
    with pytest.raises(NothingToAssign, match="assignee_id"):
        FakeBackend(tickets={7: {"id": 7}}).assign_ticket(ticket_id=7)


def test_api_backend_assign_ticket_uses_the_documented_path():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["method"] = request.method
        return httpx.Response(200, json={"ticket": {"id": 7}})

    b = ApiBackend(_client(handler))
    b.assign_ticket(ticket_id=7, group_id=9)
    assert seen["method"] == "PUT"
    assert seen["path"] == "/api/v2/tickets/7"


def test_fake_backend_assign_ticket_sets_the_group_only():
    fake = FakeBackend(tickets={7: {"id": 7}})
    result = fake.assign_ticket(ticket_id=7, group_id=9)
    assert result == {"ticket": {"id": 7, "group_id": 9}}


def test_fake_backend_assign_ticket_sets_both_fields():
    fake = FakeBackend(tickets={7: {"id": 7}})
    result = fake.assign_ticket(ticket_id=7, assignee_id=42, group_id=9)
    assert result == {"ticket": {"id": 7, "assignee_id": 42, "group_id": 9}}


def test_fake_backend_assign_ticket_raises_not_found_for_an_unknown_ticket_id():
    with pytest.raises(exc.NotFound):
        FakeBackend().assign_ticket(ticket_id=999, assignee_id=42)
