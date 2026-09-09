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
        """The active policy, or None for an ungated backend."""
        return getattr(self._backend, "policy", None)

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
