"""`ZendeskClient` - the library's public surface. The MCP server is one consumer.

Thin by design: it forwards to a (usually policy-wrapped) Backend and returns raw
envelopes. Shaping into TypedDicts happens in the MCP tool layer, which is where
the model-facing contract lives.
"""

from __future__ import annotations

from typing import cast

from .backend import Backend, Envelope
from .policy import Policy, PolicyBackend


class ZendeskClient:
    """Typed, thin surface over a Backend.

    Accepts a bare `Backend` or a `PolicyBackend`. A bare backend is ungated, and
    `.policy` says so by returning None rather than implying a policy exists.
    """

    def __init__(self, backend: Backend | PolicyBackend) -> None:
        self._backend = backend

    @property
    def policy(self) -> Policy | None:
        """The active policy, or None for an ungated backend.

        `Backend` is a structural Protocol, so an embedder's implementation may
        carry an unrelated attribute called `policy`. Duck-typing on the name
        alone would return it and quietly break this property's own annotation,
        so the type is checked: anything that is not a `Policy` reads as
        "unpoliced", which is the safe answer and a true one.
        """
        found = getattr(self._backend, "policy", None)
        return found if isinstance(found, Policy) else None

    def get_ticket(self, *, ticket_id: int) -> Envelope:
        """One ticket, as the raw upstream envelope: `{"ticket": {...}}`."""
        # `PolicyBackend.get_ticket` is materialised dynamically (policy.py's
        # `_materialise_gated_methods`), so mypy can only see it through
        # `PolicyBackend.__getattr__`, which returns `Any` - a plain
        # `result: Any = ...; return result` still trips mypy --strict's
        # warn-return-any (the *expression's* type is Any regardless of the
        # variable's declared type), so the return is cast to the type the
        # Backend Protocol actually promises instead.
        return cast(Envelope, self._backend.get_ticket(ticket_id=ticket_id))

    def search_tickets(self, *, query: str, page: int = 1, per_page: int = 25) -> Envelope:
        """Search results, as the raw upstream envelope: `{"results": [...], "count": ...}`.

        Offset paging only (`page`/`per_page`); Zendesk stops answering past
        1000 results (API-SURFACE.md §5.2), and the backend refuses a
        combination that would exceed that ceiling before making the request
        rather than let an opaque HTTP 422 escape.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(Envelope, self._backend.search_tickets(query=query, page=page, per_page=per_page))

    def list_comments(self, *, ticket_id: int) -> Envelope:
        """A ticket's comments, as the raw upstream envelope: `{"comments": [...]}`.

        Each comment's `public` flag is passed through exactly as Zendesk sent
        it (API-SURFACE.md §5.4f: it has no fixed default and inherits from the
        ticket's first comment) - never flattened, since a reader needs to know
        which prior messages actually reached the customer.

        Takes no paging parameter. API-SURFACE.md §5.4g: the endpoint caps at
        100 comments per page and defaults to oldest-first, so a ticket with
        more than 100 will have its newest comments missing from this envelope,
        with nothing in the envelope's shape announcing the gap.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(Envelope, self._backend.list_comments(ticket_id=ticket_id))
