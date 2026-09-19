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

    Adding a method here obliges three things: an `ApiBackend` implementation, a
    `FakeBackend` implementation, and a `policy._GATES` entry. The gate table
    fails closed, so a missing entry turns the method off rather than leaving it
    ungoverned.
    """

    def get_ticket(self, *, ticket_id: int) -> Envelope: ...

    def search_tickets(self, *, query: str, page: int = 1, per_page: int = 25) -> Envelope: ...


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
        return self._http.get("/api/v2/search", params={"query": query, "page": page, "per_page": per_page})


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
