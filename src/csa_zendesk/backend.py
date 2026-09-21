"""The seam.

`Backend` is a Protocol. Every method is keyword-only, so `PolicyBackend` can wrap
uniformly, and every method returns the RAW upstream envelope - shaping belongs to
the delivery layer. That is what lets one wrapper gate every method, and it is why
ADR-002 rejects every object-mapping library.

`FakeBackend` powers every unit test. A method added to the Protocol or to
`ApiBackend` but not to the fake would leave the suite exercising a stale double,
so `tests/test_backend.py` compares their signatures.
"""

from __future__ import annotations

import copy
from typing import Any, Protocol, runtime_checkable

from . import exceptions as exc
from ._http import HttpClient

Envelope = dict[str, Any]

#: Search stops being reliably retrievable past this many results, however large
#: `count` reports the true total to be (API-SURFACE.md §5.2, probe-verified):
#: page=100/per_page=10 (last item 1000) succeeds; page=101/per_page=10 (last item
#: 1010) returns HTTP 422 "Requested response size was greater than Search
#: Response Limits". A named constant, not a bare 1000 in an expression, so the
#: ceiling's provenance stays attached to it wherever it is checked.
#:
#: This bounds the PRODUCT page*per_page only - both probes above held per_page
#: fixed at 10, so they say nothing about whether per_page carries its own
#: ceiling independent of the product. See API-SURFACE.md §5.2's "Open question"
#: for what is and is not measured, and the one probe that would settle it.
SEARCH_RESULT_CEILING = 1000


def _refuse_past_search_ceiling(*, page: int, per_page: int) -> None:
    """Refuse a page/per_page combination before it reaches the wire.

    Shared by `ApiBackend` and `FakeBackend` so the two cannot drift apart -
    a fake that let this through would pass tests the real API rejects with an
    opaque 422. Failing here instead turns that 422 into an explanation that
    names the ceiling as the vendor's and points at the uncapped alternative.
    """
    last_item = page * per_page
    if last_item > SEARCH_RESULT_CEILING:
        raise exc.SearchLimitExceeded(
            f"page={page}, per_page={per_page} would reach result {last_item}, past Zendesk's "
            f"own {SEARCH_RESULT_CEILING}-result search ceiling (API-SURFACE.md §5.2). Only the "
            f"first {SEARCH_RESULT_CEILING} results are retrievable this way, however large "
            f"`count` reports the true total to be - use search/export instead, which is "
            f"cursor-paginated and uncapped."
        )


@runtime_checkable
class Backend(Protocol):
    """Every Zendesk operation this library reaches, unshaped.

    Adding a method here obliges FOUR things: an `ApiBackend` implementation, a
    `FakeBackend` implementation, a `policy._GATES` entry, and a `tools.TOOLS`
    entry. The gate table fails closed, so a missing `_GATES` entry turns the
    method off rather than leaving it ungoverned - but a `_GATES` entry with no
    `tools.TOOLS` entry behind it is its own hole: `list_comments` once shipped
    gated on `ticket.read` with no `ToolSpec`, so `assert_subject_permitted`'s
    `tools.TOOLS.get(tool)` returned `None` and the read allowlist was
    decorative for that one method (`tests/test_tools.py::
    test_every_gated_backend_method_has_a_tool_spec` is the cross-check that
    now catches a repeat).
    """

    def get_ticket(self, *, ticket_id: int) -> Envelope: ...

    def search_tickets(self, *, query: str, page: int = 1, per_page: int = 25) -> Envelope: ...

    def list_comments(self, *, ticket_id: int) -> Envelope: ...

    def update_ticket(self, *, ticket_id: int, fields: dict[str, Any]) -> Envelope: ...

    def assign_ticket(
        self, *, ticket_id: int, assignee_id: int | None = None, group_id: int | None = None
    ) -> Envelope: ...


class ApiBackend:
    """The real thing."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def get_ticket(self, *, ticket_id: int) -> Envelope:
        # analysis/operation-inventory.csv row: ticketing,Tickets,GET,
        # /api/v2/tickets/{ticket_id},ShowTicket,Show Ticket,,,yes - no .json
        # suffix. Confirmed against specs/zendesk-support-oas.yaml (operationId
        # ShowTicket), whose own response example's `url` field also omits it.
        # Only the unrelated Countries family carries a .json suffix anywhere in
        # the inventory; it is not a general convention to imitate here.
        return self._http.get(f"/api/v2/tickets/{ticket_id}")

    def search_tickets(self, *, query: str, page: int = 1, per_page: int = 25) -> Envelope:
        # analysis/operation-inventory.csv row: ticketing,Search,GET,/api/v2/search,
        # ListSearchResults,List Search Results,,,yes - no .json suffix. Confirmed
        # against specs/zendesk-support-oas.yaml (operationId ListSearchResults,
        # path /api/v2/search). Offset paging only (API-SURFACE.md §5.2): `page`
        # and `per_page`, never `page[size]`/`page[after]`/`page[before]` - this
        # endpoint answers cursor keys with HTTP 400, unlike search/export where
        # cursor paging works.
        _refuse_past_search_ceiling(page=page, per_page=per_page)
        # Important 5 (final whole-branch review): unconstrained, this endpoint
        # answers `type:user`/`type:organization` too - the end-user directory,
        # names/emails/notes included - while this tool backs `ticket.read`
        # alone; `E1_CAPABILITIES` deliberately excludes `PEOPLE_READ`. ADR-016's
        # own rule is that a tool is (operation x the constraint that fixes its
        # impact), and a `search_tickets` that can return users is not
        # bucket-pure. `constrained_query` COMPOSES `type:ticket` onto whatever
        # the caller sent rather than inspecting it for an existing `type:` and
        # rewriting - inspection is a string match a caller can out-guess (a
        # different case, extra whitespace, ...), the same class of bypass
        # `_untrusted.py` rejects string-matching for. Composition instead relies
        # on Zendesk's own search grammar: repeating a single-valued field ANDs
        # the occurrences together, and no ticket record can simultaneously be a
        # `user`, so a caller who also writes `type:user` gets a query that can
        # never match anything - narrowed to nothing, never widened to users. A
        # caller cannot make this tool's bucket bigger by asking twice.
        constrained_query = f"{query} type:ticket"
        return self._http.get("/api/v2/search", params={"query": constrained_query, "page": page, "per_page": per_page})

    def list_comments(self, *, ticket_id: int) -> Envelope:
        # analysis/operation-inventory.csv row: ticketing,Ticket Comments,GET,
        # /api/v2/tickets/{ticket_id}/comments,ListTicketComments,List Comments,
        # cursor,,yes - no .json suffix, consistent with get_ticket; only the
        # unrelated Countries family carries one anywhere in the inventory, so
        # neither presence nor absence generalises from it.
        #
        # No paging parameter: API-SURFACE.md §5.4g - this endpoint caps at 100
        # records per page and defaults to oldest-first, so a ticket with more
        # than 100 comments silently omits its newest ones here. This method
        # does not add a paging parameter the brief did not ask for; a caller
        # reading a long ticket must not assume the result is complete.
        return self._http.get(f"/api/v2/tickets/{ticket_id}/comments")

    def update_ticket(self, *, ticket_id: int, fields: dict[str, Any]) -> Envelope:
        # analysis/operation-inventory.csv row: ticketing,Tickets,PUT,
        # /api/v2/tickets/{ticket_id},UpdateTicket,Update Ticket,,,yes - no
        # .json suffix, matching get_ticket; confirmed against
        # specs/zendesk-support-oas.yaml (operationId UpdateTicket). Only the
        # unrelated Countries family carries a .json suffix anywhere in the
        # inventory - get_ticket's own comment already records that the
        # suffix does not generalise in either direction.
        #
        # This is the operation ADR-016 was written about: PUT here is five
        # impact levels in one call (field edit, internal note, public reply,
        # solve, close), decided entirely by the request body. What keeps
        # THIS call inside the field-edit bucket is tools.TOOLS["update_ticket"]'s
        # `_forbid("comment", "status", ...)` constraint, enforced at the
        # policy/tool seam (policy._dispatch) before this method is ever
        # reached - this method itself sends whatever `fields` it is given,
        # unshaped, per ADR-002.
        return self._http.request("PUT", f"/api/v2/tickets/{ticket_id}", json={"ticket": fields})

    def assign_ticket(self, *, ticket_id: int, assignee_id: int | None = None, group_id: int | None = None) -> Envelope:
        # Same operation and path as update_ticket - analysis/operation-inventory.csv
        # row: ticketing,Tickets,PUT,/api/v2/tickets/{ticket_id},UpdateTicket,
        # Update Ticket,,,yes. Bucket-pure by construction rather than by
        # enumeration (analysis/SLICE-FINDINGS.md): tools.TOOLS["assign_ticket"]'s
        # `_only("assignee_id", "group_id")` is an allowlist, so this method
        # only ever needs to build a body from those two keys - there is no
        # forbidden-key surface here that could fall behind as the OAS grows,
        # unlike update_ticket's denylist.
        fields: dict[str, Any] = {}
        if assignee_id is not None:
            fields["assignee_id"] = assignee_id
        if group_id is not None:
            fields["group_id"] = group_id
        return self._http.request("PUT", f"/api/v2/tickets/{ticket_id}", json={"ticket": fields})


class FakeBackend:
    """In-memory double, faithful to the shapes observed live.

    It returns full envelopes (`{"ticket": {...}}`), not inner objects, and it
    raises the same typed errors the real backend does - a fake that fails
    differently is worse than no fake.

    Both the constructor and every getter deep-copy: a real ticket envelope is
    nested nearly everywhere (`via`, `satisfaction_rating`, `custom_fields`,
    `fields`), and a shallow `dict(...)` only protects the top level - a caller
    mutating a nested field through a returned envelope would otherwise corrupt
    both this fixture's backing store and the dict the constructor was given.
    Envelopes are JSON, so `copy.deepcopy` is exactly the right semantics, and
    the cost is irrelevant in a test double.
    """

    def __init__(self, tickets: dict[int, dict[str, Any]] | None = None) -> None:
        self.tickets: dict[int, dict[str, Any]] = copy.deepcopy(tickets) if tickets else {}

    def get_ticket(self, *, ticket_id: int) -> Envelope:
        try:
            return {"ticket": copy.deepcopy(self.tickets[ticket_id])}
        except KeyError:
            raise exc.NotFound(f"no such record (ticket {ticket_id})") from None

    def search_tickets(self, *, query: str, page: int = 1, per_page: int = 25) -> Envelope:
        # Canned, shaped like the real envelope (`results`, `count`) - this fake
        # does not implement Zendesk's query language against `self.tickets`.
        # It still enforces the same 1000-result ceiling `ApiBackend` does: a
        # fake that let this through would pass tests the real API rejects.
        _refuse_past_search_ceiling(page=page, per_page=per_page)
        return {"results": [], "count": 0}

    def list_comments(self, *, ticket_id: int) -> Envelope:
        # Canned, like search_tickets: this fake does not maintain a per-ticket
        # comment store. It does share get_ticket's existence check against
        # self.tickets, so a ticket_id nothing has ever heard of still raises
        # NotFound rather than a silent, misleadingly-empty conversation.
        if ticket_id not in self.tickets:
            raise exc.NotFound(f"no such record (ticket {ticket_id})")
        return {"comments": []}

    def update_ticket(self, *, ticket_id: int, fields: dict[str, Any]) -> Envelope:
        # Mutates the backing store, unlike search_tickets/list_comments'
        # canned replies - a caller of update_ticket needs to see its own
        # write reflected on the next get_ticket, the same way the real API
        # would show it. Still deep-copies both in and out (this class's own
        # docstring), so neither the caller's `fields` dict nor the returned
        # envelope alias the fixture's backing store.
        try:
            ticket = self.tickets[ticket_id]
        except KeyError:
            raise exc.NotFound(f"no such record (ticket {ticket_id})") from None
        ticket.update(copy.deepcopy(fields))
        return {"ticket": copy.deepcopy(ticket)}

    def assign_ticket(self, *, ticket_id: int, assignee_id: int | None = None, group_id: int | None = None) -> Envelope:
        try:
            ticket = self.tickets[ticket_id]
        except KeyError:
            raise exc.NotFound(f"no such record (ticket {ticket_id})") from None
        if assignee_id is not None:
            ticket["assignee_id"] = assignee_id
        if group_id is not None:
            ticket["group_id"] = group_id
        return {"ticket": copy.deepcopy(ticket)}
