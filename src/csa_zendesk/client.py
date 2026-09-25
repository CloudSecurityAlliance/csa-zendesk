"""`ZendeskClient` - the library's public surface. The MCP server is one consumer.

Thin by design: it forwards to a (usually policy-wrapped) Backend and returns raw
envelopes. Shaping into TypedDicts happens in the MCP tool layer, which is where
the model-facing contract lives.
"""

from __future__ import annotations

from typing import Any, cast

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

    def update_ticket(self, *, ticket_id: int, fields: dict[str, Any]) -> Envelope:
        """Edit ticket fields, as the raw upstream envelope: `{"ticket": {...}}`.

        Never a comment and never a status change - `PUT /tickets/{id}` is
        five impact levels in one call (ADR-016), and it is the tool/policy
        constraint (`tools.TOOLS["update_ticket"]`'s `_forbid("comment",
        "status", ...)`), enforced at the `PolicyBackend` seam, that keeps
        this call inside the field-edit bucket - not this method, which just
        forwards `fields` unshaped.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(Envelope, self._backend.update_ticket(ticket_id=ticket_id, fields=fields))

    def assign_ticket(
        self, *, ticket_id: int, assignee_id: int | None = None, group_id: int | None = None, unassign: bool = False
    ) -> Envelope:
        """Reassign a ticket's owner and/or group, as the raw upstream envelope.

        Same operation as `update_ticket` (`PUT /tickets/{id}`), but bucket-pure
        by construction: `tools.TOOLS["assign_ticket"]`'s `_only("assignee_id",
        "group_id")` is an allowlist, so no other field can reach this call.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(
            Envelope,
            self._backend.assign_ticket(
                ticket_id=ticket_id, assignee_id=assignee_id, group_id=group_id, unassign=unassign
            ),
        )

    def add_internal_note(self, *, ticket_id: int, body: str, uploads: list[str] | None = None) -> Envelope:
        """Add a private, internal-only comment, as the raw upstream envelope.

        Always private, never merely defaulted so: API-SURFACE.md §5.4f -
        comment.public has no fixed default and inherits from the ticket's
        first comment, which is PUBLIC on an email-originated ticket. This
        method (and `Backend.add_internal_note` beneath it) takes no
        `public` parameter at all, so there is no channel through which a
        caller - or an instruction injected from ticket content - could make
        this call reach the requester; `ApiBackend`/`FakeBackend` hardcode
        `public: False` unconditionally.

        `uploads` is a list of upload tokens (Task 4's `upload_file` return
        value). An empty list and `None` behave identically - neither puts
        an `uploads` key in the request body.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(Envelope, self._backend.add_internal_note(ticket_id=ticket_id, body=body, uploads=uploads))

    def reply_publicly(self, *, ticket_id: int, body: str, uploads: list[str] | None = None) -> Envelope:
        """Reply PUBLICLY: an email to the requester that cannot be unsent.

        Same shape as `add_internal_note`, with `public` forced to `True`
        instead of `False` in the backend. There is no `public` parameter here
        either - the asymmetry between these two methods is which tool you
        called, never an argument you passed.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(Envelope, self._backend.reply_publicly(ticket_id=ticket_id, body=body, uploads=uploads))

    def solve_ticket(self, *, ticket_id: int, custom_fields: list[dict[str, Any]] | None = None) -> Envelope:
        """Mark a ticket solved, as the raw upstream envelope: `{"ticket": {...}}`.

        `status=solved` is forced and cannot be changed through this call.

        `custom_fields` carries the fields a tenant's ticket form requires at
        solve time (F7): without it this method could not solve any ticket on
        such a tenant. Supplying data Zendesk demands is not the same as
        choosing what the call does, so the status stays forced.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(Envelope, self._backend.solve_ticket(ticket_id=ticket_id, custom_fields=custom_fields))

    def upload_file(self, *, filename: str, content: bytes, content_type: str) -> Envelope:
        """Upload a file's bytes, as the raw upstream envelope: `{"upload": {"token": ...}}`.

        This is not itself an attachment: the returned token names bytes that
        exist on Zendesk's side attached to nothing at all, and become
        visible on a ticket only once passed to `add_internal_note(uploads=
        [token])`. `filename` must carry an extension - Zendesk requires it
        to match the real file's, or a reader may fail to open the result -
        and a call omitting one is refused before it reaches the wire.

        An unattached upload is invisible everywhere else in this library's
        surface; use `delete_upload` to clean one up rather than leaving it
        as litter nobody can find.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(Envelope, self._backend.upload_file(filename=filename, content=content, content_type=content_type))

    def delete_upload(self, *, token: str) -> Envelope:
        """Delete an unattached upload by its token, as the raw upstream envelope.

        The cleanup counterpart to `upload_file`: an upload that is never
        attached to a comment is otherwise invisible litter.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(Envelope, self._backend.delete_upload(token=token))

    def get_attachment(self, *, attachment_id: int) -> Envelope:
        """Read one attachment's metadata, as the raw upstream envelope: `{"attachment": {...}}`.

        A read, not an attach operation - it does not create or delete
        anything, so it needs only `ticket.read`.
        """
        # See get_ticket's comment above on why this needs an explicit cast.
        return cast(Envelope, self._backend.get_attachment(attachment_id=attachment_id))
