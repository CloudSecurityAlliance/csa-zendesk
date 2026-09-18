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


@runtime_checkable
class Backend(Protocol):
    """Every Zendesk operation this library reaches, unshaped.

    Adding a method here obliges three things: an `ApiBackend` implementation, a
    `FakeBackend` implementation, and a `policy._GATES` entry. The gate table
    fails closed, so a missing entry turns the method off rather than leaving it
    ungoverned.
    """

    def get_ticket(self, *, ticket_id: int) -> Envelope: ...


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
