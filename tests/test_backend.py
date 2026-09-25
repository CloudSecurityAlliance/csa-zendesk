import inspect
import json
from typing import Any

import httpx
import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk._http import HttpClient
from csa_zendesk.backend import ApiBackend, Backend, FakeBackend


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

        def add_internal_note(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

        def reply_publicly(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

        def solve_ticket(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

        def upload_file(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

        def delete_upload(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
            return {}

        def get_attachment(self, *args: object, **kwargs: object) -> dict:  # wrong shape entirely
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


def test_api_backend_update_ticket_refuses_an_empty_fields_mapping_before_the_call():
    # Same defect as assign_ticket's empty case, in the sibling method:
    # tools.TOOLS["update_ticket"]'s _forbid(...) is a denylist and says
    # nothing about `fields` being empty, and a caller holding a bare Backend
    # never passes through tools.TOOLS at all (ADR-002's public seam). Same
    # before-the-call shape as test_a_request_past_the_thousand_result_
    # ceiling_is_refused_before_the_call.
    called = {"n": 0}

    def handler(request):  # pragma: no cover - must never run
        called["n"] += 1
        return httpx.Response(200, json={})

    b = ApiBackend(_client(handler))
    with pytest.raises(exc.EmptyWrite, match="fields"):
        b.update_ticket(ticket_id=7, fields={})
    assert called["n"] == 0


def test_fake_backend_update_ticket_refuses_an_empty_fields_mapping_too():
    # Shares _refuse_an_empty_update with ApiBackend, the same way search's
    # ceiling check is shared - a fake that let this through would pass a
    # call the real backend rejects outright.
    with pytest.raises(exc.EmptyWrite, match="fields"):
        FakeBackend(tickets={7: {"id": 7}}).update_ticket(ticket_id=7, fields={})


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
    with pytest.raises(exc.EmptyWrite, match="assignee_id"):
        b.assign_ticket(ticket_id=7)
    assert called["n"] == 0


def test_fake_backend_assign_ticket_refuses_an_empty_assignment_too():
    # Shares _refuse_an_empty_assignment with ApiBackend, the same way
    # search's ceiling check is shared - a fake that let this through would
    # pass a call the real backend rejects outright.
    with pytest.raises(exc.EmptyWrite, match="assignee_id"):
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


# --- add_internal_note: same PUT, public forced by construction, never by input


def test_api_backend_add_internal_note_sends_a_private_comment():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ticket": {"id": 7}})

    b = ApiBackend(_client(handler))
    b.add_internal_note(ticket_id=7, body="internal note")
    assert seen["method"] == "PUT"
    assert seen["path"] == "/api/v2/tickets/7"
    assert seen["body"] == {"ticket": {"comment": {"body": "internal note", "public": False}}}


def test_api_backend_add_internal_note_has_no_public_parameter_to_override():
    # THE control this block exists to get right (API-SURFACE §5.4f): there is
    # no `public` argument here at all for a caller - or an instruction
    # injected from ticket content the model is reading - to set, so it
    # cannot be flipped true by any well-formed call. TypeError, not
    # PolicyError, proves the parameter is simply absent from the signature.
    def handler(request):  # pragma: no cover - must never run
        return httpx.Response(200, json={})

    b = ApiBackend(_client(handler))
    with pytest.raises(TypeError):
        b.add_internal_note(ticket_id=7, body="hi", public=True)  # type: ignore[call-arg]


def test_api_backend_add_internal_note_sends_uploads_when_given():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ticket": {"id": 7}})

    b = ApiBackend(_client(handler))
    b.add_internal_note(ticket_id=7, body="see attached", uploads=["tok1", "tok2"])
    assert seen["body"] == {
        "ticket": {"comment": {"body": "see attached", "public": False, "uploads": ["tok1", "tok2"]}}
    }


def test_api_backend_add_internal_note_treats_an_empty_list_and_none_uploads_identically():
    # Task 3 brief: "An empty list and None must behave identically - neither
    # should put an uploads key in the request body."
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ticket": {"id": 7}})

    b = ApiBackend(_client(handler))
    b.add_internal_note(ticket_id=7, body="hi", uploads=None)
    b.add_internal_note(ticket_id=7, body="hi", uploads=[])
    assert bodies[0] == bodies[1] == {"ticket": {"comment": {"body": "hi", "public": False}}}
    assert "uploads" not in bodies[0]["ticket"]["comment"]


def test_api_backend_add_internal_note_returns_the_envelope_unshaped():
    body = {"ticket": {"id": 7, "comment": {"id": 99, "public": False}}}

    def handler(request):
        return httpx.Response(200, json=body)

    assert ApiBackend(_client(handler)).add_internal_note(ticket_id=7, body="hi") == body


def test_api_backend_add_internal_note_refuses_an_empty_note_before_the_call():
    called = {"n": 0}

    def handler(request):  # pragma: no cover - must never run
        called["n"] += 1
        return httpx.Response(200, json={})

    b = ApiBackend(_client(handler))
    with pytest.raises(exc.EmptyWrite, match="body"):
        b.add_internal_note(ticket_id=7, body="")
    assert called["n"] == 0


def test_api_backend_add_internal_note_refuses_a_whitespace_only_body_with_no_uploads():
    def handler(request):  # pragma: no cover - must never run
        return httpx.Response(200, json={})

    with pytest.raises(exc.EmptyWrite, match="body"):
        ApiBackend(_client(handler)).add_internal_note(ticket_id=7, body="   ")


def test_api_backend_add_internal_note_permits_an_empty_body_when_an_upload_is_attached():
    # Task 4 attaches files by passing uploads=[token] here - a note that is
    # "just the attachment" is legitimate, unlike a note that is nothing at all.
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ticket": {"id": 7}})

    b = ApiBackend(_client(handler))
    b.add_internal_note(ticket_id=7, body="", uploads=["tok1"])
    assert seen["body"] == {"ticket": {"comment": {"body": "", "public": False, "uploads": ["tok1"]}}}


def test_fake_backend_add_internal_note_returns_the_ticket_unchanged():
    fake = FakeBackend(tickets={7: {"id": 7, "subject": "hello"}})
    assert fake.add_internal_note(ticket_id=7, body="internal") == {"ticket": {"id": 7, "subject": "hello"}}


def test_fake_backend_add_internal_note_raises_not_found_for_an_unknown_ticket_id():
    with pytest.raises(exc.NotFound):
        FakeBackend().add_internal_note(ticket_id=999, body="hi")


def test_fake_backend_add_internal_note_refuses_an_empty_note_too():
    # Shares _refuse_an_empty_note with ApiBackend - a fake that let this
    # through would pass a call the real backend rejects outright.
    with pytest.raises(exc.EmptyWrite, match="body"):
        FakeBackend(tickets={7: {"id": 7}}).add_internal_note(ticket_id=7, body="")


def test_fake_backend_add_internal_note_checks_emptiness_before_the_existence_lookup():
    # Matches update_ticket/assign_ticket: the refusal does not depend on
    # whether ticket_id is real.
    with pytest.raises(exc.EmptyWrite, match="body"):
        FakeBackend().add_internal_note(ticket_id=999, body="")


# --- solve_ticket: same PUT, status is the only thing this call can send -----


def test_api_backend_solve_ticket_sends_status_solved_only():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ticket": {"id": 7, "status": "solved"}})

    b = ApiBackend(_client(handler))
    result = b.solve_ticket(ticket_id=7)
    assert seen["method"] == "PUT"
    assert seen["path"] == "/api/v2/tickets/7"
    assert seen["body"] == {"ticket": {"status": "solved"}}
    assert result == {"ticket": {"id": 7, "status": "solved"}}


def test_api_backend_solve_ticket_returns_the_envelope_unshaped():
    body = {"ticket": {"id": 7, "status": "solved", "custom_fields": [{"id": 1, "value": None}]}}

    def handler(request):
        return httpx.Response(200, json=body)

    assert ApiBackend(_client(handler)).solve_ticket(ticket_id=7) == body


def test_fake_backend_solve_ticket_mutates_and_returns_the_ticket():
    fake = FakeBackend(tickets={7: {"id": 7, "status": "open"}})
    result = fake.solve_ticket(ticket_id=7)
    assert result == {"ticket": {"id": 7, "status": "solved"}}
    assert fake.get_ticket(ticket_id=7)["ticket"]["status"] == "solved"


def test_fake_backend_solve_ticket_raises_not_found_for_an_unknown_ticket_id():
    with pytest.raises(exc.NotFound):
        FakeBackend().solve_ticket(ticket_id=999)


# --- upload_file: the two-step upload's first half - a token, attached to nothing


def test_upload_sends_the_filename_as_a_query_parameter_and_bytes_as_the_body():
    # Path from analysis/operation-inventory.csv row: ticketing,Attachments,POST,
    # /api/v2/uploads,UploadFiles,Upload Files,,,
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["content"] = request.content
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(201, json={"upload": {"token": "abc123"}})

    b = ApiBackend(_client(handler))
    out = b.upload_file(filename="report.pdf", content=b"%PDF-1.7 fake", content_type="application/pdf")
    assert "filename=report.pdf" in seen["url"]
    assert seen["content"] == b"%PDF-1.7 fake"
    assert seen["content_type"] == "application/pdf"
    assert out == {"upload": {"token": "abc123"}}


def test_upload_refuses_a_filename_with_no_extension():
    # The spec requires the uploaded filename's extension to match the real
    # file's; a filename with none cannot satisfy that, and the failure would
    # surface as an unopenable attachment rather than an API error.
    called = {"n": 0}

    def handler(request):  # pragma: no cover - must never run
        called["n"] += 1
        return httpx.Response(201, json={})

    with pytest.raises(exc.InvalidFilename, match="extension"):
        ApiBackend(_client(handler)).upload_file(filename="report", content=b"x", content_type="application/pdf")
    assert called["n"] == 0


def test_upload_refuses_a_filename_that_is_only_a_trailing_dot():
    # os.path.splitext("report.") == ("report", ".") - a dot with nothing
    # after it to call an extension, the same defect as no dot at all.
    called = {"n": 0}

    def handler(request):  # pragma: no cover - must never run
        called["n"] += 1
        return httpx.Response(201, json={})

    with pytest.raises(exc.InvalidFilename, match="extension"):
        ApiBackend(_client(handler)).upload_file(filename="report.", content=b"x", content_type="application/pdf")
    assert called["n"] == 0


def test_upload_is_not_retried_on_503():
    # Task 4 decision, carried forward from Task 1's review: a retried upload
    # does not repeat a no-op the way a retried PUT does - it mints a SECOND
    # token, a second orphaned file nothing in the ticket surface would ever
    # show. Proven here at the Backend seam, not only at _http/_transport's
    # own idempotent=False plumbing: exactly one request must reach the wire.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={}, headers={"Retry-After": "0"})

    with pytest.raises(exc.ServiceUnavailable):
        ApiBackend(_client(handler)).upload_file(filename="report.pdf", content=b"x", content_type="application/pdf")
    assert calls["n"] == 1


def test_fake_backend_upload_file_returns_a_canned_token():
    # Canned, like search_tickets/list_comments: no self.tickets-shaped store
    # exists for an orphaned upload to be checked against.
    assert FakeBackend().upload_file(filename="report.pdf", content=b"x", content_type="application/pdf") == {
        "upload": {"token": "fake-upload-token"}
    }


def test_fake_backend_upload_file_refuses_a_filename_with_no_extension():
    # The fake enforces the same pre-flight refusal ApiBackend does: a fake
    # that let this through would pass tests the real API rejects.
    with pytest.raises(exc.InvalidFilename, match="extension"):
        FakeBackend().upload_file(filename="report", content=b"x", content_type="application/pdf")


# --- delete_upload: cleanup for an upload that was never attached -------------


def test_delete_upload_targets_the_token():
    # Path from analysis/operation-inventory.csv row: ticketing,Attachments,
    # DELETE,/api/v2/uploads/{token},DeleteUpload,Delete Upload,,,
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(204)

    ApiBackend(_client(handler)).delete_upload(token="abc123")
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/api/v2/uploads/abc123")


def test_delete_upload_returns_the_envelope_unshaped():
    # 204 No Content -> {} (ZD-2, _http._envelope): success with nothing to report.
    assert ApiBackend(_client(lambda r: httpx.Response(204))).delete_upload(token="abc123") == {}


def test_fake_backend_delete_upload_returns_an_empty_envelope():
    # Canned: no per-upload store exists to remove `token` from.
    assert FakeBackend().delete_upload(token="abc123") == {}


# --- get_attachment: reading an already-attached file's metadata is a read ----


def test_get_attachment_calls_the_documented_path():
    # Path from analysis/operation-inventory.csv row: ticketing,Attachments,
    # GET,/api/v2/attachments/{attachment_id},ShowAttachment,Show Attachment,,,
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["method"] = request.method
        return httpx.Response(200, json={"attachment": {"id": 42}})

    assert ApiBackend(_client(handler)).get_attachment(attachment_id=42) == {"attachment": {"id": 42}}
    assert seen["method"] == "GET"
    assert seen["path"] == "/api/v2/attachments/42"


def test_fake_backend_get_attachment_returns_a_canned_envelope():
    # Canned: no per-attachment store exists to look attachment_id up in.
    assert FakeBackend().get_attachment(attachment_id=42) == {"attachment": {"id": 42}}


def test_upload_refuses_empty_content_before_the_call():
    # Not merely an empty write like its siblings: Zendesk ACCEPTS a zero-byte
    # upload and returns a token, so the failure is silent - an attachment that
    # downloads as nothing, and an orphan no other tool can list.
    called = {"n": 0}

    def handler(request):  # pragma: no cover - must never run
        called["n"] += 1
        return httpx.Response(201, json={})

    with pytest.raises(exc.EmptyWrite, match="non-empty content"):
        ApiBackend(_client(handler)).upload_file(filename="r.pdf", content=b"", content_type="application/pdf")
    assert called["n"] == 0


def test_the_fake_refuses_empty_content_too():
    # A fake that accepted zero bytes would let the refusal pass every test
    # while doing nothing in production - the same reason it enforces the
    # extension rule.
    with pytest.raises(exc.EmptyWrite, match="non-empty content"):
        FakeBackend().upload_file(filename="r.pdf", content=b"", content_type="application/pdf")


@pytest.mark.parametrize(
    "token",
    [
        "../tickets/159143",  # the original finding
        "%2e%2e/tickets/1",  # the same, percent-escaped
        "..",
        "a/b",
        "",
        "a b",
        "<script>",
    ],
)
def test_delete_upload_refuses_a_token_that_could_address_something_else(token):
    # THIS TEST REPLACES ONE THAT ASSERTED THE BUG. The first fix quoted the
    # token with safe="" and asserted the encoded form was SENT - but `quote`
    # runs BEFORE `_http._validate_path`, and it encodes "/" to "%2F", so the
    # dot-segment check had no separators left to split on. The encoding hid
    # the traversal from the guard meant to catch it, and the test then pinned
    # that as correct. The two were described as independent layers; they are
    # in series, and the second blinded the first.
    #
    # The token is now validated as a VALUE, before anything encodes it.
    called = {"n": 0}

    def handler(request):  # pragma: no cover - must never run
        called["n"] += 1
        return httpx.Response(200, json={})

    with pytest.raises(exc.InvalidPath, match="upload token"):
        ApiBackend(_client(handler)).delete_upload(token=token)
    assert called["n"] == 0


@pytest.mark.parametrize("token", [12345, None, ["a"], {"a": 1}])
def test_delete_upload_refuses_a_token_that_is_not_a_string(token):
    # MCP arguments arrive from JSON and the SDK does not validate inputSchema,
    # so a non-string reaches here. Before this it raised a bare TypeError out
    # of `quote`, escaping the error contract `_on_call_tool` relies on.
    def handler(request):  # pragma: no cover - must never run
        return httpx.Response(200, json={})

    with pytest.raises(exc.InvalidPath, match="upload token"):
        ApiBackend(_client(handler)).delete_upload(token=token)


def test_delete_upload_sends_an_ordinary_token_unchanged():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    ApiBackend(_client(handler)).delete_upload(token="abc-123_XYZ")
    assert seen[0].endswith("/api/v2/uploads/abc-123_XYZ")


def test_add_internal_note_is_not_retried_on_503():
    # The one write here that APPENDS rather than setting a target state.
    # Retrying update_ticket/assign_ticket/solve_ticket re-sends the same
    # desired state and converges; retrying this adds a second identical note,
    # and a fourth after three retries, each with its own audit entry.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={}, headers={"Retry-After": "0"})

    with pytest.raises(exc.ServiceUnavailable):
        ApiBackend(_client(handler)).add_internal_note(ticket_id=1, body="hi", uploads=None)
    assert calls["n"] == 1, "an appending write must not be replayed"


def test_the_state_setting_writes_are_still_retried_on_503():
    # The other half, so the change above is a decision about THIS method
    # rather than a blanket switch nobody notices: a PUT that sets a target
    # state converges on replay and should keep retrying.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={}, headers={"Retry-After": "0"})

    with pytest.raises(exc.ServiceUnavailable):
        ApiBackend(_client(handler)).solve_ticket(ticket_id=1)
    assert calls["n"] > 1, "a state-setting PUT should still be retried"


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("get_ticket", {"ticket_id": "../../users/5"}),
        ("get_ticket", {"ticket_id": "%2e%2e/%2e%2e/users/5"}),
        ("list_comments", {"ticket_id": "../x"}),
        ("get_attachment", {"attachment_id": "%2e%2e/tickets/1"}),
        ("solve_ticket", {"ticket_id": "%2e%2e/users/5"}),
        ("update_ticket", {"ticket_id": "../x", "fields": {"priority": "high"}}),
        ("assign_ticket", {"ticket_id": "../x", "assignee_id": 7, "group_id": None}),
        ("add_internal_note", {"ticket_id": "../x", "body": "hi", "uploads": None}),
    ],
)
def test_no_id_that_is_not_a_number_reaches_a_path(method, kwargs):
    # `Backend` annotates these `int` and nothing enforced it: MCP arguments
    # arrive from JSON, and mcp 2.2.0's low-level Server does NOT validate
    # `inputSchema`, so `"type": "integer"` is documentation rather than a
    # control. Enforced at the seam, not at the delivery layer, because the
    # library is callable without going through the server at all.
    called = {"n": 0}

    def handler(request):  # pragma: no cover - must never run
        called["n"] += 1
        return httpx.Response(200, json={})

    with pytest.raises(exc.InvalidPath, match="whole number"):
        getattr(ApiBackend(_client(handler)), method)(**kwargs)
    assert called["n"] == 0


#: A BOM, not a ZWSP: `_markdown._STRIP` deliberately does NOT strip U+200B
#: (Thai/Khmer word segmentation - see `_markdown.strip_suspicious`'s
#: docstring), so a ZWSP fixture here would assert stripping that Task 3's
#: corrected `_STRIP` set no longer performs. A mid-document BOM IS in
#: `_STRIP` (same codepoint `tests/test_markdown.py`'s own
#: `test_codepoints_with_no_communicative_purpose_are_removed` and
#: `test_to_markdown_strips_in_both_the_visible_and_hidden_output` fixtures
#: use), so this still proves what the test is for: that `to_markdown`'s
#: codepoint stripping flows through the Backend seam, not just the
#: conversion.
BOM = "﻿"


def _comment_html(html):
    return {"comments": [{"id": 1, "public": True, "body": "plain", "html_body": html}]}


def test_list_comments_returns_markdown_not_html():
    def handler(request):
        return httpx.Response(200, json=_comment_html("<p>Hello <strong>world</strong></p>"))

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert "**world**" in cm["html_body"]
    assert "<p>" not in cm["html_body"]


def test_hidden_text_arrives_in_its_own_key_and_not_in_the_body():
    def handler(request):
        return httpx.Response(200, json=_comment_html('<p>Refund please.</p><div style="display:none">SECRET</div>'))

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert "SECRET" not in cm["html_body"]
    assert cm["hidden_text"] == ["SECRET"]


def test_no_hidden_key_when_there_is_no_hidden_text():
    # A key present on every comment with an empty list is noise on ~96% of them.
    def handler(request):
        return httpx.Response(200, json=_comment_html("<p>ordinary</p>"))

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert "hidden_text" not in cm


def test_plain_body_is_left_alone():
    # `body` is Zendesk's own tag strip and is NOT the source of truth here,
    # but it is also not ours to rewrite - callers may rely on it verbatim.
    #
    # Minor 4 (final whole-branch review): the name promised `plain_body`
    # coverage, but the fixture (`_comment_html`) never carried a `plain_body`
    # key at all and the assertion below was on `body` - `body` IS the more
    # important field (it is what a caller actually reads when it is not
    # converting `html_body` itself), so the coverage was right and the name
    # was wrong. Building the envelope directly here, rather than adding
    # `plain_body` to the shared `_comment_html` helper every other test in
    # this file also uses, keeps this fix scoped to the one test it names.
    def handler(request):
        return httpx.Response(
            200,
            json={
                "comments": [
                    {"id": 1, "public": True, "body": "plain", "plain_body": "plain too", "html_body": "<p>x</p>"}
                ]
            },
        )

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert cm["body"] == "plain"
    assert cm["plain_body"] == "plain too"


def test_a_ticket_description_is_converted_too():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "ticket": {
                    "id": 1,
                    "subject": "s",
                    "description": "d",
                    "html_body": f"<p>tick{BOM}et</p>",
                }
            },
        )

    t = ApiBackend(_client(handler)).get_ticket(ticket_id=1)["ticket"]
    assert BOM not in t["html_body"]
    assert "<p>" not in t["html_body"]


def test_body_and_plain_body_are_stripped_of_suspicious_codepoints():
    """H5. `html_body` is converted; these two ride along beside it, undefended.

    They carry Zendesk's own plain-text rendering, which keeps a Trojan Source
    override exactly as the sender wrote it. Measured live in F2: the U+202E
    was stripped from `html_body` while the same character survived in both of
    these. A model reads whichever field it likes, so defanging one of three
    renderings is defanging none.
    """
    rlo = "\u202e"

    def handler(request):
        return httpx.Response(
            200,
            json={
                "comments": [
                    {
                        "id": 1,
                        "public": True,
                        "html_body": f"<p>before{rlo}after</p>",
                        "body": f"before{rlo}after",
                        "plain_body": f"before{rlo}after",
                    }
                ]
            },
        )

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert rlo not in cm["html_body"], "html_body regressed"
    assert rlo not in cm["body"], "body still carries the override"
    assert rlo not in cm["plain_body"], "plain_body still carries the override"
    assert cm["body"] == "beforeafter"


def test_stripping_body_leaves_ordinary_non_english_text_untouched():
    """The strip must be invisible on real prose, including non-English prose.

    Persian ZWNJ is semantic, an emoji ZWJ sequence is one glyph, and an
    accented Latin character is ordinary. None may be touched. This is the set
    `_STRIP` was narrowed to after an earlier version damaged seven legitimate
    cases, and the narrowing is the point: a strip that mangles Persian to
    catch an attack nobody has sent is a bad trade.
    """
    ordinary = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645 caf\u00e9 \U0001f469\u200d\U0001f4bb"

    def handler(request):
        return httpx.Response(
            200,
            json={"comments": [{"id": 1, "public": True, "body": ordinary, "plain_body": ordinary}]},
        )

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert cm["body"] == ordinary
    assert cm["plain_body"] == ordinary


def test_a_body_that_is_not_a_string_is_left_alone():
    """Zendesk returns `null` for some bodies; the strip must not crash on one."""

    def handler(request):
        return httpx.Response(
            200,
            json={"comments": [{"id": 1, "public": True, "body": None, "plain_body": 12345}]},
        )

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert cm["body"] is None
    assert cm["plain_body"] == 12345


def test_a_converted_body_says_it_was_converted():
    """DEC-021. A tool description is read once, by a model that may not be the
    one holding this result. The payload is the consumer's only account of what
    it has, and until now it did not mention that every `html_body` had been
    rewritten.
    """

    def handler(request):
        return httpx.Response(200, json=_comment_html("<p>Hello <strong>world</strong></p>"))

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    (conversion,) = [t for t in cm["transformations"] if t["action"] == "converted"]
    assert conversion["field"] == "html_body"
    assert conversion["rule"] == "dec-018/convert-on-ingest"
    assert "text/html" in conversion["detail"] and "text/markdown" in conversion["detail"]


def test_removed_concealed_elements_are_disclosed_with_a_count():
    """`hidden_text` already says WHAT was concealed. This says it was removed
    from the body, by which rule, and how many - which `hidden_text` alone does
    not, because a reader cannot tell a two-element removal from one element
    containing two paragraphs.
    """

    def handler(request):
        return httpx.Response(
            200,
            json=_comment_html('<p>Hi.</p><div style="display:none">A</div><span style="font-size:0">B</span>'),
        )

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    (removal,) = [t for t in cm["transformations"] if t["action"] == "removed"]
    assert removal["rule"] == "hidden-element/inline-style"
    assert "2" in removal["detail"]
    assert len(cm["hidden_text"]) == 2


def test_stripped_codepoints_name_their_class_not_just_the_fact():
    """Three classes live in `_STRIP` and they are not the same signal. A
    Trojan Source override being present is worth a reader's attention; a stray
    control character is housekeeping. A single "stripped" rule would flatten
    them into each other.
    """

    def handler(request):
        return httpx.Response(
            200,
            json={"comments": [{"id": 1, "public": True, "body": "before\u202eafter", "plain_body": "x\u0007y"}]},
        )

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    by_field = {t["field"]: t for t in cm["transformations"] if t["action"] == "stripped"}
    assert by_field["body"]["rule"] == "codepoint/bidi-override"
    assert by_field["plain_body"]["rule"] == "codepoint/control-character"


def test_nothing_changed_means_no_disclosure_key_at_all():
    """DEC-021 is explicit that the block is absent when nothing happened, so
    silence is a claim rather than the absence of one - and so this does not
    become another key on every record, which this project has already measured
    the cost of.
    """

    def handler(request):
        return httpx.Response(200, json={"comments": [{"id": 1, "public": True, "body": "ordinary text"}]})

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert "transformations" not in cm


def test_solve_ticket_can_carry_the_custom_fields_a_form_requires():
    """F7. `solve_ticket` took only a ticket id, so on a tenant whose form
    requires fields at solve time it could not solve ANY ticket - measured
    twice, on two different fixtures, with byte-identical refusals.

    That is the difference between rung E2 and a usable server: you can
    triage, note, assign and attach, and then cannot close the loop.
    """
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"ticket": {"id": 1, "status": "solved"}})

    ApiBackend(_client(handler)).solve_ticket(ticket_id=1, custom_fields=[{"id": 42, "value": "x"}])
    assert sent["ticket"]["custom_fields"] == [{"id": 42, "value": "x"}]


def test_solve_ticket_still_forces_solved_and_a_caller_cannot_choose_otherwise():
    """The invariant the original design protected, kept.

    `status: "solved"` is hardcoded for the same reason `add_internal_note`
    hardcodes `public: false`: the tool's whole identity is that one effect.
    Carrying custom_fields alongside adds the data Zendesk demands; it does not
    make the status negotiable.
    """
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"ticket": {"id": 1}})

    ApiBackend(_client(handler)).solve_ticket(ticket_id=1, custom_fields=[{"id": 7, "value": "a"}])
    assert sent["ticket"]["status"] == "solved"
    assert set(sent["ticket"]) == {"status", "custom_fields"}


def test_omitting_custom_fields_sends_no_key_at_all():
    """A tenant with no required fields must not start receiving an empty list."""
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"ticket": {"id": 1}})

    ApiBackend(_client(handler)).solve_ticket(ticket_id=1)
    assert sent["ticket"] == {"status": "solved"}


def test_a_solve_refused_for_missing_fields_says_which_and_how_to_supply_them():
    """DEC-020's shape: a refusal names what tripped and what would proceed.

    Zendesk answers with its own `details` map, which is accurate and says
    nothing about THIS tool - a caller reading it has no way to know
    `custom_fields` is the argument that fixes it. Measured: a model asked to
    solve a ticket discovers the pairing only by failing first.
    """

    def handler(request):
        return httpx.Response(
            422,
            json={
                "error": "RecordInvalid",
                "description": "Record validation errors",
                "details": {
                    "base": [
                        {"description": "Department: is required when solving a ticket"},
                        {"description": "Allocation: is required when solving a ticket"},
                    ]
                },
            },
        )

    with pytest.raises(exc.ValidationError) as caught:
        ApiBackend(_client(handler)).solve_ticket(ticket_id=1)
    message = str(caught.value)
    assert "custom_fields" in message, "the refusal does not name the argument that would proceed"
    assert "Department" in message, "the refusal does not carry which fields Zendesk named"


def test_a_validation_error_that_is_not_about_solving_is_re_raised_untouched():
    """The guidance must attach to the ONE case it describes.

    A ticket refused for an unrelated validation problem gets Zendesk's own
    message, not advice about `custom_fields` that would send the caller
    chasing the wrong thing.
    """

    def handler(request):
        return httpx.Response(
            422,
            json={
                "error": "RecordInvalid",
                "description": "Record validation errors",
                "details": {"priority": [{"description": "Priority: is not a valid value"}]},
            },
        )

    with pytest.raises(exc.ValidationError) as caught:
        ApiBackend(_client(handler)).solve_ticket(ticket_id=1)
    assert "custom_fields" not in str(caught.value)
    assert "Priority" in str(caught.value)


def test_the_fake_stores_custom_fields_so_a_later_read_shows_them():
    """A double that accepted the argument and dropped it would let every test
    pass while the real path was broken - which is the failure mode the whole
    Backend-seam design exists to prevent.
    """
    fake = FakeBackend(tickets={1: {"id": 1, "status": "open"}})
    fake.solve_ticket(ticket_id=1, custom_fields=[{"id": 9, "value": "done"}])
    assert fake.tickets[1]["custom_fields"] == [{"id": 9, "value": "done"}]
    assert fake.tickets[1]["status"] == "solved"


def test_the_solve_refusal_carries_field_ids_when_it_can_get_them():
    """A label is not what the API takes. The refusal used to name `Department`
    and leave the caller to find its id by hand, which is a second lookup in a
    different system at the moment they are already stuck.
    """
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "ticket_fields" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "ticket_fields": [
                        {"id": 111, "title": "Department"},
                        {"id": 222, "title": "Allocation"},
                        {"id": 333, "title": "Unrelated"},
                    ]
                },
            )
        return httpx.Response(
            422,
            json={
                "error": "RecordInvalid",
                "details": {
                    "base": [
                        {"description": "Department: is required when solving a ticket"},
                        {"description": "Allocation: is required when solving a ticket"},
                    ]
                },
            },
        )

    with pytest.raises(exc.ValidationError) as caught:
        ApiBackend(_client(handler)).solve_ticket(ticket_id=1)
    message = str(caught.value)
    assert "111" in message and "222" in message, "field ids are missing from the refusal"
    assert "333" not in message, "an unrelated field leaked into the refusal"


def test_the_refusal_still_works_when_the_field_lookup_is_refused():
    """The enrichment is a courtesy, never a dependency.

    Reading ticket-field configuration is rung E3 territory, so a credential
    that cannot do it must still get the actionable refusal rather than a
    second, more confusing error from the lookup itself.
    """

    def handler(request):
        if "ticket_fields" in request.url.path:
            return httpx.Response(403, json={"error": "Forbidden"})
        return httpx.Response(
            422,
            json={
                "error": "RecordInvalid",
                "details": {
                    "base": [
                        {"description": "Department: is required when solving a ticket"},
                    ]
                },
            },
        )

    with pytest.raises(exc.ValidationError) as caught:
        ApiBackend(_client(handler)).solve_ticket(ticket_id=1)
    message = str(caught.value)
    assert "custom_fields" in message, "the remedy was lost when enrichment failed"
    assert "Department" in message


def test_unassign_is_an_explicit_argument_not_a_none():
    """G4. Assignment was a one-way door: `update_ticket` refuses `assignee_id`
    (it belongs to `assign_ticket`, ADR-016) and `assign_ticket()` naming
    neither argument is refused as an empty write. Both correct alone; together
    nothing at rung E2 could unassign.

    `None` cannot mean "clear" here, because it already means "not supplied" and
    would collide with the empty-write refusal. So clearing is its own argument.
    """
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"ticket": {"id": 1, "assignee_id": None}})

    ApiBackend(_client(handler)).assign_ticket(ticket_id=1, unassign=True)
    assert sent["ticket"]["assignee_id"] is None


def test_unassign_refuses_to_be_combined_with_an_assignee():
    """The two say opposite things; a call meaning both is a caller error, not a
    precedence puzzle for this library to resolve silently.
    """
    with pytest.raises(exc.ZendeskError) as caught:
        ApiBackend(_client(lambda r: httpx.Response(200, json={}))).assign_ticket(
            ticket_id=1, assignee_id=5, unassign=True
        )
    assert "unassign" in str(caught.value).lower()


def test_unassign_alone_is_not_an_empty_write():
    """The empty-write refusal exists so a call that changes nothing does not
    spend the tenant's rate budget and land in the audit log. `unassign=True`
    changes something, so it must pass.
    """

    def handler(request):
        return httpx.Response(200, json={"ticket": {"id": 1}})

    ApiBackend(_client(handler)).assign_ticket(ticket_id=1, unassign=True)


def test_reassigning_to_a_different_agent_still_works():
    """The ordinary path, pinned beside the new one."""
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"ticket": {"id": 1}})

    ApiBackend(_client(handler)).assign_ticket(ticket_id=1, assignee_id=77)
    assert sent["ticket"]["assignee_id"] == 77


def test_a_solve_refusal_with_no_parseable_label_still_gets_the_remedy():
    """The two checks are deliberately different strengths.

    `_is_required_on_solve` matches the phrase; the label extractor also needs a
    colon to split on. Zendesk wording that satisfies the first and not the
    second must still produce the actionable refusal - just without ids - rather
    than falling through to the raw vendor message.
    """

    def handler(request):
        if "ticket_fields" in request.url.path:  # pragma: no cover - not reached
            return httpx.Response(200, json={"ticket_fields": []})
        return httpx.Response(
            422,
            json={
                "error": "RecordInvalid",
                "details": {
                    "base": [
                        {"description": "A field is required when solving a ticket"},
                    ]
                },
            },
        )

    with pytest.raises(exc.ValidationError) as caught:
        ApiBackend(_client(handler)).solve_ticket(ticket_id=1)
    message = str(caught.value)
    assert "custom_fields" in message
    assert "come from this tenant's ticket form" in message


def test_reply_publicly_forces_public_true_the_same_way_a_note_forces_false():
    """E5. The mirror of `add_internal_note`, and deliberately the same shape.

    `comment.public` has NO fixed default - it inherits from the ticket's first
    comment - so on an email-originated ticket it defaults to PUBLIC. Forcing it
    here means the reach of this call never depends on the ticket's history.
    """
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"ticket": {"id": 1}})

    ApiBackend(_client(handler)).reply_publicly(ticket_id=1, body="Thanks, resolved.")
    assert sent["ticket"]["comment"]["public"] is True
    assert sent["ticket"]["comment"]["body"] == "Thanks, resolved."


def test_public_is_not_an_argument_anyone_can_name():
    """Structural, not checked.

    `add_internal_note` moved off a `comment`-dict constraint precisely so that
    `public` is not a key a caller - or an instruction injected from ticket
    content the model is reading - can set at all. That argument is STRONGER
    for the tool that can actually reach a customer, so the riskier tool gets
    the stronger guarantee rather than the weaker one.
    """
    import inspect

    params = inspect.signature(ApiBackend.reply_publicly).parameters
    assert "public" not in params
    assert set(params) == {"self", "ticket_id", "body", "uploads"}


def test_reply_publicly_refuses_an_empty_body():
    """Same refusal as a note: an empty public reply still emails someone."""
    with pytest.raises(exc.EmptyWrite):
        ApiBackend(_client(lambda r: httpx.Response(200, json={}))).reply_publicly(ticket_id=1, body="   ")


def test_reply_publicly_can_carry_uploads():
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"ticket": {"id": 1}})

    ApiBackend(_client(handler)).reply_publicly(ticket_id=1, body="see attached", uploads=["tok"])
    assert sent["ticket"]["comment"]["uploads"] == ["tok"]
    assert sent["ticket"]["comment"]["public"] is True


def test_the_fake_records_the_public_reply_rather_than_discarding_it():
    """A double that accepted the call and kept no evidence would let a test
    assert success while proving nothing about the thing that matters - whether
    this reached anyone.
    """
    fake = FakeBackend(tickets={1: {"id": 1, "status": "open"}})
    fake.reply_publicly(ticket_id=1, body="we have shipped a fix")
    assert fake.public_replies == [{"ticket_id": 1, "body": "we have shipped a fix", "public": True}]


def test_the_fake_refuses_a_public_reply_to_a_ticket_it_does_not_have():
    fake = FakeBackend(tickets={})
    with pytest.raises(exc.NotFound):
        fake.reply_publicly(ticket_id=99, body="hello")


def test_the_fake_refuses_an_empty_public_reply():
    fake = FakeBackend(tickets={1: {"id": 1}})
    with pytest.raises(exc.EmptyWrite):
        fake.reply_publicly(ticket_id=1, body="")


def test_the_fake_converts_too():
    # A fake that returned raw HTML would let every markdown assertion pass
    # while doing nothing in production - the reason the fake enforces the
    # extension and empty-upload rules too.
    fake = FakeBackend()
    fake.tickets[1] = {"id": 1, "html_body": "<p>hi <em>there</em></p>"}
    out = fake.get_ticket(ticket_id=1)["ticket"]
    assert "*there*" in out["html_body"] and "<em>" not in out["html_body"]


# --- Important 1 (final whole-branch review): the write tools were not wired ---
# `update_ticket`/`assign_ticket`/`add_internal_note`/`solve_ticket` all return
# a `TicketUpdateResponse` (`{audit, ticket}` - specs/zendesk-support-oas.yaml),
# and an audit Comment event can carry a FRESH `html_body` authored by a
# trigger or automation firing on this very update - not just this call's own
# note. Before this fix only get_ticket/search_tickets/list_comments converted,
# so raw HTML from a trigger-authored comment reached the model through these
# four tools.


@pytest.mark.parametrize(
    "call",
    [
        lambda b: b.update_ticket(ticket_id=7, fields={"priority": "high"}),
        lambda b: b.assign_ticket(ticket_id=7, assignee_id=42),
        lambda b: b.add_internal_note(ticket_id=7, body="hi"),
        lambda b: b.solve_ticket(ticket_id=7),
    ],
    ids=["update_ticket", "assign_ticket", "add_internal_note", "solve_ticket"],
)
def test_every_write_tool_converts_html_body_in_its_audit_event(call):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "audit": {
                    "events": [{"id": 1, "type": "Comment", "html_body": "<p>hi <em>there</em></p>"}],
                },
                "ticket": {"id": 7},
            },
        )

    result = call(ApiBackend(_client(handler)))
    converted = result["audit"]["events"][0]["html_body"]
    assert "*there*" in converted
    assert "<em>" not in converted


# --- Important 3 (final whole-branch review): RecursionError must not escape ---


def test_deeply_nested_html_is_left_unconverted_rather_than_raising():
    # `to_markdown` recurses through BeautifulSoup's parsed tree; nesting past
    # ~495 <div>s overflows Python's recursion limit and raises RecursionError
    # - which IS a RuntimeError, so it matches no branch in server.py's
    # _on_call_tool except-chain and would otherwise escape this library as a
    # bare, untyped exception. Caught at the seam so ONE field fails rather
    # than the whole envelope/call.
    nested = "<div>" * 1000 + "hi" + "</div>" * 1000

    def handler(request):
        return httpx.Response(200, json=_comment_html(nested))

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert cm["html_body"] == nested  # left unconverted, not blanked
    assert cm["body"] == "plain"  # untouched - the agent can still read the comment
    assert cm["hidden_text"] == [
        "csa-zendesk: html_body could not be converted to Markdown (nested too deeply) - left as raw, unconverted HTML."
    ]
