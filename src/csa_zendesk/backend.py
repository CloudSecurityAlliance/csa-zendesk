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
import os
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

from . import _markdown
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


def _refuse_an_empty_assignment(*, assignee_id: int | None, group_id: int | None) -> None:
    """Refuse an `assign_ticket` call naming neither field, before it reaches the wire.

    Shared by `ApiBackend` and `FakeBackend`, the same way `_refuse_past_search_ceiling`
    is above, so the two cannot drift apart. Raises `exc.EmptyWrite` - centralised in
    `exceptions.py` alongside its pre-flight, own-prose siblings (`InvalidPath`,
    `SearchLimitExceeded`), not local to this module: `_scope.AllowlistError` was the
    one local exception in this codebase, not a convention to extend, and a second
    local type would have started making an outlier look like a pattern.
    """
    if assignee_id is None and group_id is None:
        raise exc.EmptyWrite(
            "assign_ticket needs assignee_id, group_id, or both - a call naming neither "
            "would send an empty write to Zendesk: no effect, but it still spends this "
            "tenant's write-rate budget and still lands in the ticket's audit log as an "
            "update that changed nothing."
        )


def _refuse_an_empty_update(*, fields: dict[str, Any]) -> None:
    """Refuse an `update_ticket` call whose `fields` mapping is empty, before it
    reaches the wire. `assign_ticket`'s sibling check, for the same reason:
    shared by `ApiBackend` and `FakeBackend` so the two cannot drift apart.
    """
    if not fields:
        raise exc.EmptyWrite(
            "update_ticket needs a non-empty fields mapping - a call with nothing in it "
            "would send an empty write to Zendesk: no effect, but it still spends this "
            "tenant's write-rate budget and still lands in the ticket's audit log as an "
            "update that changed nothing."
        )


def _refuse_an_empty_note(*, body: str, uploads: list[str] | None) -> None:
    """Refuse an `add_internal_note` call carrying neither text nor an
    attachment, before it reaches the wire. Same reasoning as
    `_refuse_an_empty_update`/`_refuse_an_empty_assignment`, and shared by
    `ApiBackend` and `FakeBackend` for the same reason.

    A whitespace-only body is treated as empty (`.strip()`), not merely an
    absent one - a body of `"   "` conveys nothing a reader could act on
    either. `uploads=[]` and `uploads=None` are equivalent here, the same as
    everywhere else this parameter is threaded (Task 4 attaches files by
    passing tokens here): both are falsy, so a call with an empty list and no
    body is refused exactly like a call with neither argument at all.
    """
    if not body.strip() and not uploads:
        raise exc.EmptyWrite(
            "add_internal_note needs a non-empty body, at least one upload, or both - a call "
            "with neither would send an empty write to Zendesk: no content added, but it still "
            "spends this tenant's write-rate budget and still lands in the ticket's audit log as "
            "an update that changed nothing."
        )


def _path_id(value: object, *, name: str) -> str:
    """Coerce an id to its decimal form before it is interpolated into a path.

    `Backend`'s signatures annotate these `int`, and nothing enforced the
    annotation: MCP tool arguments arrive from JSON, and mcp 2.2.0's low-level
    `Server` does NOT validate `inputSchema`, so `"type": "integer"` is
    documentation rather than a control. A string therefore reached an f-string
    path unchecked, which is what made the traversal findings reachable at all.

    This restores the property `_http._validate_path`'s docstring relies on -
    "every caller interpolates an int" - at the seam rather than at the
    delivery layer, because the library is callable without going through the
    server. `int(value)` accepts a bool, which is harmless here (it stringifies
    to 0/1 and addresses nothing), and refuses everything else.
    """
    try:
        return str(int(value))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        raise exc.InvalidPath(
            f"{name} must be a whole number, not {value!r} - it is interpolated into the request "
            f"path, so a value that is not a number could change which endpoint is addressed."
        ) from None


# A Zendesk upload token is an opaque alphanumeric string. Anchored and
# whole-value, NOT a scan for bad characters: a denylist over a value that
# becomes part of a URL is the same unclosable shape as the field denylist this
# branch already replaced with an allowlist.
_UPLOAD_TOKEN = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def _refuse_an_unsafe_upload_token(*, token: object) -> None:
    """Refuse a `delete_upload` token that could address something other than an upload.

    THE ORDER HERE IS THE WHOLE POINT, and getting it wrong is how the first fix
    for this failed. `quote(token, safe="")` encodes `/` to `%2F`, so a token
    quoted BEFORE reaching `_http._validate_path` has no separators left for
    that function's `split("/")` to find - the encoding hid the traversal from
    the choke point meant to catch it. The two were described as independent
    layers; they are in series, and the second blinded the first.

    So the value is validated here, as a value, before anything encodes it.
    `quote` stays as the belt: with this check the traversal never arrives, and
    if this check is ever loosened the encoding still stops the path resolving
    client-side.
    """
    if not isinstance(token, str) or not _UPLOAD_TOKEN.match(token):
        raise exc.InvalidPath(
            f"refusing upload token {token!r}: an upload token is alphanumeric (with - and _). "
            f"This value is interpolated into the request path, so one containing a separator, a "
            f"percent-escape or a dot segment could address a different endpoint entirely."
        )


def _refuse_an_empty_upload(*, content: bytes) -> None:
    """Refuse an `upload_file` call carrying no bytes, before it reaches the wire.

    `_refuse_an_empty_note`'s sibling, and shared by `ApiBackend` and `FakeBackend`
    for the same reason. An empty upload is worse than the empty writes those
    siblings refuse, not merely equivalent: Zendesk accepts it, so the call
    *succeeds* and mints a real token naming zero bytes. Nothing in the ticket
    surface lists an unattached upload, so the only way that token is ever seen
    again is if a later `add_internal_note` attaches it - at which point a reader
    gets an attachment that downloads as nothing, with no error anywhere to
    explain it.

    This became reachable the moment `server.py` began accepting the bytes as
    base64 text: `base64.b64decode` discards non-alphabet characters by default,
    so some malformed input decodes to `b""` rather than raising. That call site
    now passes `validate=True`, which is the better fix; this is the second one,
    at the seam, because the library is callable without going through the
    server at all.
    """
    if not content:
        raise exc.EmptyWrite(
            "upload_file needs a non-empty content - Zendesk accepts a zero-byte upload and "
            "returns a token for it, so this does not fail loudly: it produces an attachment "
            "that downloads as nothing, and an orphaned token that no other tool in this "
            "surface can list."
        )


def _refuse_a_filename_without_extension(*, filename: str) -> None:
    """Refuse an `upload_file` call whose `filename` carries no extension,
    before it reaches the wire.

    Zendesk's upload documentation requires the filename passed here to share
    an extension with the real file's content - a mismatch, or an absence,
    "could give an error when attempting to open the attachment." A filename
    with no extension at all cannot satisfy that, so it is refused here
    rather than left to surface later as an unopenable attachment instead of
    a clean, pre-flight error. Shared by `ApiBackend` and `FakeBackend`, the
    same way `_refuse_an_empty_update` and its siblings above are, and for
    the same reason: the two backends must refuse identically.

    Deliberately does not attempt to compare `content_type` against the
    extension: an "obvious" mismatch (e.g. `content_type="image/png"` with
    `filename="report.pdf"`) is not reliably detectable - a caller may
    legitimately upload a PDF under a generic `content_type`, or a filename
    whose extension is technically valid but unusual for its content - and a
    check that is right most of the time but wrong sometimes would refuse a
    legitimate upload with no way to override it. Presence of an extension is
    the one property this can check without guessing at the caller's intent.
    """
    # os.path.splitext treats a name with no extension - and a dotfile like
    # ".bashrc", whose leading dot is not a separator - as ("<name>", ""), so
    # a falsy `ext` covers both "no dot at all" and "a leading dot with
    # nothing after it to call an extension."
    _, ext = os.path.splitext(filename)
    if not ext or ext == ".":
        raise exc.InvalidFilename(
            f"upload_file needs a filename with an extension; got {filename!r}. Zendesk requires "
            f"the uploaded filename's extension to match the real file's, or the agent's browser "
            f"or file reader could fail to open the attachment - a filename with no extension at "
            f"all cannot satisfy that."
        )


def _convert_html_bodies(envelope: Envelope) -> Envelope:
    """Replace every `html_body` in `envelope` with Markdown, in place.

    Walks the whole envelope rather than naming ticket/comment shapes, because
    `html_body` appears on tickets, comments, audit events and search results,
    and a shape-specific walk would silently miss the next one.

    Adds a sibling `hidden_text` list ONLY when there is hidden text - a key
    present on every comment with an empty list is noise on the ~96% that carry
    none (measured).

    Applied at the Backend seam, not in `server.py`: the library is callable
    without the MCP server, and a control in the delivery layer is one a library
    consumer does not get.
    """
    if isinstance(envelope, dict):
        html = envelope.get("html_body")
        if isinstance(html, str):
            try:
                markdown, hidden = _markdown.to_markdown(html)
            except RecursionError:
                # Important 3 (final whole-branch review): `to_markdown` recurses
                # through BeautifulSoup's parsed tree, and deeply nested HTML
                # (measured: ~495 levels of `<div>`) overflows Python's recursion
                # limit. `RecursionError` IS a `RuntimeError`, so it matches no
                # branch in `server.py`'s `_on_call_tool` except-chain and would
                # otherwise escape this library as a bare, untyped exception - a
                # library consumer gets a crash from `get_ticket`, not a typed
                # error.
                #
                # Fail this ONE field, not the whole envelope: `body` is untouched
                # by this block (this function never reads or writes it), so the
                # agent can still read the comment via Zendesk's own plain-text
                # rendering even when `html_body` cannot be converted.
                #
                # `html_body` is left UNCONVERTED (the original raw HTML), not
                # blanked - and a library-authored note goes in `hidden_text` so a
                # reader (human or model) can tell "conversion failed, this is raw
                # HTML below" from "ordinary Markdown", rather than silently
                # returning something that merely looks like a normal field.
                envelope["hidden_text"] = [
                    "csa-zendesk: html_body could not be converted to Markdown (nested too "
                    "deeply) - left as raw, unconverted HTML."
                ]
            else:
                envelope["html_body"] = markdown
                if hidden:
                    envelope["hidden_text"] = hidden
        # `hidden_text` above is added BEFORE this loop starts, not inside it:
        # `.values()` is a live view over the dict, and adding a key to a dict
        # while an iterator over it is active raises `RuntimeError: dictionary
        # changed size during iteration`. Both mutations to THIS dict (the
        # `html_body` overwrite and the possible `hidden_text` insert) are
        # already done by the time the loop below opens its iterator, so the
        # dict's size is stable for the whole walk.
        for value in envelope.values():
            _convert_html_bodies(value)
    elif isinstance(envelope, list):
        for item in envelope:
            _convert_html_bodies(item)
    return envelope


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

    def add_internal_note(self, *, ticket_id: int, body: str, uploads: list[str] | None = None) -> Envelope: ...

    def solve_ticket(self, *, ticket_id: int) -> Envelope: ...

    def upload_file(self, *, filename: str, content: bytes, content_type: str) -> Envelope: ...

    def delete_upload(self, *, token: str) -> Envelope: ...

    def get_attachment(self, *, attachment_id: int) -> Envelope: ...


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
        return _convert_html_bodies(self._http.get(f"/api/v2/tickets/{_path_id(ticket_id, name='ticket_id')}"))

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
        return _convert_html_bodies(
            self._http.get("/api/v2/search", params={"query": constrained_query, "page": page, "per_page": per_page})
        )

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
        return _convert_html_bodies(self._http.get(f"/api/v2/tickets/{_path_id(ticket_id, name='ticket_id')}/comments"))

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
        # `_only_fields(*_TICKET_EDITABLE_FIELDS)` constraint, enforced at the
        # policy/tool seam (policy._dispatch) before this method is ever
        # reached - this method itself sends whatever `fields` it is given,
        # unshaped, per ADR-002.
        #
        # CORRECTED (post-Task-3 review): that constraint must run against
        # THIS `fields` dict, not against the call's own top-level kwargs
        # (`{"ticket_id", "fields"}`) - the two are different levels of the
        # same call, and `_forbid` was originally wired to the wrong one, so
        # `update_ticket(ticket_id=X, fields={"comment": {"public": True}})`
        # sailed through unchecked and reached Zendesk as a public reply.
        # `tools.ToolSpec.body_key="fields"` on this tool's entry is what
        # makes `policy._dispatch` extract `fields` before calling `check` -
        # see that field's docstring for the full incident.
        #
        # An EMPTY `fields`, though, is refused here rather than sent: the
        # tool's constraint is an allowlist over key NAMES, and an empty dict
        # trivially satisfies it (no key is outside the allowed set), and a
        # bare-Backend caller never
        # passes through tools.TOOLS at all (ADR-002's public seam). See
        # exc.EmptyWrite's docstring for why a no-op write is not free.
        _refuse_an_empty_update(fields=fields)
        # Important 1 (final whole-branch review): this call's response is a
        # `TicketUpdateResponse` (`{audit, ticket}` - specs/zendesk-support-oas.yaml),
        # and an audit Comment event can carry a fresh `html_body` - a trigger or
        # automation firing on this very update can author one, so it must be
        # converted here exactly as it is on every read path. `_convert_html_bodies`
        # walks the whole envelope, so it finds `audit.events[].html_body` without
        # this method needing to know that shape specifically.
        return _convert_html_bodies(
            self._http.request(
                "PUT", f"/api/v2/tickets/{_path_id(ticket_id, name='ticket_id')}", json={"ticket": fields}
            )
        )

    def assign_ticket(self, *, ticket_id: int, assignee_id: int | None = None, group_id: int | None = None) -> Envelope:
        # Same operation and path as update_ticket - analysis/operation-inventory.csv
        # row: ticketing,Tickets,PUT,/api/v2/tickets/{ticket_id},UpdateTicket,
        # Update Ticket,,,yes. Bucket-pure by construction rather than by
        # enumeration (analysis/SLICE-FINDINGS.md): tools.TOOLS["assign_ticket"]'s
        # `_only("assignee_id", "group_id")` is an allowlist, so this method
        # only ever needs to build a body from those two keys - there is no
        # forbidden-key surface here that could fall behind as the OAS grows,
        # unlike update_ticket's allowlist.
        #
        # Refused here, at the Backend seam, rather than trusted to the tool
        # layer alone: `_only("assignee_id", "group_id")` permits any SUBSET
        # of those keys, including the empty one, and a caller holding a bare
        # Backend never passes through tools.TOOLS at all (ADR-002's public
        # seam). See exc.EmptyWrite's docstring for why an empty write is
        # not free even though it changes nothing.
        _refuse_an_empty_assignment(assignee_id=assignee_id, group_id=group_id)
        fields: dict[str, Any] = {}
        if assignee_id is not None:
            fields["assignee_id"] = assignee_id
        if group_id is not None:
            fields["group_id"] = group_id
        # Important 1 (final whole-branch review): same TicketUpdateResponse shape
        # and same trigger/automation-authored html_body risk as update_ticket above.
        return _convert_html_bodies(
            self._http.request(
                "PUT", f"/api/v2/tickets/{_path_id(ticket_id, name='ticket_id')}", json={"ticket": fields}
            )
        )

    def add_internal_note(self, *, ticket_id: int, body: str, uploads: list[str] | None = None) -> Envelope:
        # Same operation and path as update_ticket/assign_ticket -
        # analysis/operation-inventory.csv row: ticketing,Tickets,PUT,
        # /api/v2/tickets/{ticket_id},UpdateTicket,Update Ticket,,,yes.
        #
        # THE CONTROL THIS BLOCK EXISTS TO GET RIGHT (API-SURFACE.md §5.4f):
        # comment.public has NO fixed default - it inherits from the ticket's
        # first comment, and a real email-originated ticket on this tenant's
        # own fixture was confirmed live to make that default PUBLIC. This
        # method takes no `public` parameter at all, and never will: rather
        # than accept one and override it, the parameter is simply absent
        # from the signature, so there is no code path - bare Backend or
        # policy-wrapped, well-formed caller or an instruction injected from
        # ticket content the model is reading - through which this call can
        # become public. `"public": False` below is unconditional.
        #
        # An EMPTY note - no body and no attachment - is refused here rather
        # than sent, same reasoning as update_ticket's empty fields and
        # assign_ticket's empty assignment: a bare-Backend caller never
        # passes through tools.TOOLS at all (ADR-002's public seam), so the
        # refusal belongs at this seam too, not only at the tool layer.
        _refuse_an_empty_note(body=body, uploads=uploads)
        # LOAD-BEARING: `"public": False` is a literal, not derived from any
        # parameter - this line IS the control (tools.TOOLS["add_internal_note"]
        # only allowlists ticket_id/body/uploads; it has nothing to force,
        # because there is no `public` argument anywhere upstream of this
        # call). If this is ever rewritten to build `comment` from a mapping
        # that could carry a caller-supplied "public" key, that rewrite
        # removes the only thing keeping this method's notes internal.
        comment: dict[str, Any] = {"body": body, "public": False}
        if uploads:
            comment["uploads"] = uploads
        # idempotent=False, unlike its three PUT siblings: this is the one
        # write here that APPENDS rather than setting a target state. Replaying
        # update_ticket/assign_ticket/solve_ticket on a 503 re-sends the same
        # desired state and converges; replaying this adds a SECOND identical
        # note, and a fourth after three retries, each with its own audit
        # entry - the duplication a human reading the ticket sees first.
        # (Final whole-branch review, Important 3: `post_binary`'s docstring
        # claimed every `request` caller was a state-setting PUT. That was true
        # when the only callers were GETs and became false in the same branch
        # that wrote it; the claim is corrected there.)
        #
        # Important 1 (final whole-branch review): the response here is the
        # same TicketUpdateResponse shape as update_ticket/assign_ticket - THIS
        # note's own audit entry always carries html_body, and any OTHER
        # trigger/automation firing on the same update could add another. Both
        # must be converted before they leave this method.
        return _convert_html_bodies(
            self._http.request(
                "PUT",
                f"/api/v2/tickets/{_path_id(ticket_id, name='ticket_id')}",
                json={"ticket": {"comment": comment}},
                idempotent=False,
            )
        )

    def solve_ticket(self, *, ticket_id: int) -> Envelope:
        # Same operation and path as update_ticket/assign_ticket -
        # analysis/operation-inventory.csv row: ticketing,Tickets,PUT,
        # /api/v2/tickets/{ticket_id},UpdateTicket,Update Ticket,,,yes.
        #
        # No `status` parameter, unlike update_ticket's allowlist or
        # assign_ticket's allowlist over two optional fields: solving is the
        # only thing this call can do, by construction - there is nothing
        # here for a caller to choose, so there is nothing to force or
        # refuse. `tools.TOOLS["solve_ticket"]`'s allowlist over `ticket_id`
        # alone (tools.py) still refuses an extra kwarg with a clean
        # PolicyError before it would otherwise reach this method as a raw
        # TypeError.
        #
        # Important 1 (final whole-branch review): same TicketUpdateResponse
        # shape and same trigger/automation-authored html_body risk as the
        # three siblings above - solving a ticket is exactly the kind of
        # update a trigger fires on.
        return _convert_html_bodies(
            self._http.request(
                "PUT",
                f"/api/v2/tickets/{_path_id(ticket_id, name='ticket_id')}",
                json={"ticket": {"status": "solved"}},
            )
        )

    def upload_file(self, *, filename: str, content: bytes, content_type: str) -> Envelope:
        # analysis/operation-inventory.csv row: ticketing,Attachments,POST,
        # /api/v2/uploads,UploadFiles,Upload Files,,,
        #
        # UPLOADING IS TWO STEPS, AND THIS IS ONLY THE FIRST (task brief): the
        # token this returns names bytes that exist on Zendesk's side attached
        # to NOTHING - it becomes visible on a ticket only once a later
        # `add_internal_note(uploads=[token])` call carries it there. This
        # call therefore reaches nobody, which is why it is gated on
        # `policy.TICKET_ATTACH` rather than `TICKET_WRITE` (see that
        # constant's own comment) and why neither this method nor its
        # `tools.TOOLS` entry names a `subject_var`: there is no ticket yet
        # to scope against, exactly like `search_tickets`.
        #
        # `filename` must carry an extension (Zendesk's own requirement -
        # see `_refuse_a_filename_without_extension`'s docstring) - refused
        # before this reaches the wire, the same pre-flight shape as every
        # other refusal in this module.
        _refuse_a_filename_without_extension(filename=filename)
        _refuse_an_empty_upload(content=content)
        # idempotent=False, EXPLICITLY, though it is also `post_binary`'s own
        # default: a retried upload does not repeat a no-op the way a retried
        # PUT does - it mints a SECOND token, a second orphaned file, that
        # nothing in the ticket surface will ever show (Task 1 review;
        # Task 4's decision - see `_http.HttpClient.post_binary`'s docstring
        # for the full reasoning). Written out here so the decision is
        # visible at the call site that matters, not only at the default.
        return self._http.post_binary(
            "/api/v2/uploads",
            content=content,
            content_type=content_type,
            params={"filename": filename},
            idempotent=False,
        )

    def delete_upload(self, *, token: str) -> Envelope:
        # analysis/operation-inventory.csv row: ticketing,Attachments,DELETE,
        # /api/v2/uploads/{token},DeleteUpload,Delete Upload,,,
        #
        # Ships in this same task, not a later one: an upload that is never
        # attached to a comment is invisible everywhere else in the ticket
        # surface (task brief) - without this method, a failed or abandoned
        # `upload_file` call (or a retried one under the decision above)
        # leaves litter nobody can find.
        # quote(safe="") is the second guard, at the interpolation site.
        # `_validate_path` refuses a dot segment for every caller, which is the
        # general fix; this one makes the specific claim that a `token` is a
        # single path SEGMENT and cannot become a path - `safe=""` encodes `/`
        # too, so `../tickets/1` becomes `..%2Ftickets%2F1` and addresses a
        # (non-existent) upload rather than a ticket. Both are kept: the choke
        # point cannot know that this particular value is model-supplied, and
        # this line cannot protect the other callers.
        _refuse_an_unsafe_upload_token(token=token)
        return self._http.request("DELETE", f"/api/v2/uploads/{quote(token, safe='')}")

    def get_attachment(self, *, attachment_id: int) -> Envelope:
        # analysis/operation-inventory.csv row: ticketing,Attachments,GET,
        # /api/v2/attachments/{attachment_id},ShowAttachment,Show Attachment,,,
        #
        # Gated on ticket.read, not ticket.attach (policy._GATES) - reading an
        # attachment already on a ticket is a read like any other; ticket.attach
        # governs creating a new, as-yet-unattached upload, not reading one that
        # already reached somewhere.
        return self._http.get(f"/api/v2/attachments/{_path_id(attachment_id, name='attachment_id')}")


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
            return _convert_html_bodies({"ticket": copy.deepcopy(self.tickets[ticket_id])})
        except KeyError:
            raise exc.NotFound(f"no such record (ticket {ticket_id})") from None

    def search_tickets(self, *, query: str, page: int = 1, per_page: int = 25) -> Envelope:
        # Canned, shaped like the real envelope (`results`, `count`) - this fake
        # does not implement Zendesk's query language against `self.tickets`.
        # It still enforces the same 1000-result ceiling `ApiBackend` does: a
        # fake that let this through would pass tests the real API rejects.
        #
        # Wrapped in `_convert_html_bodies` for symmetry with `ApiBackend`, even
        # though the canned envelope below carries no `html_body` today - this
        # is a no-op now, not a promise that stays true if the canned shape
        # ever grows one.
        _refuse_past_search_ceiling(page=page, per_page=per_page)
        return _convert_html_bodies({"results": [], "count": 0})

    def list_comments(self, *, ticket_id: int) -> Envelope:
        # Canned, like search_tickets: this fake does not maintain a per-ticket
        # comment store. It does share get_ticket's existence check against
        # self.tickets, so a ticket_id nothing has ever heard of still raises
        # NotFound rather than a silent, misleadingly-empty conversation.
        # Wrapped in `_convert_html_bodies` for the same symmetry reason as
        # search_tickets above - a no-op today.
        if ticket_id not in self.tickets:
            raise exc.NotFound(f"no such record (ticket {ticket_id})")
        return _convert_html_bodies({"comments": []})

    def update_ticket(self, *, ticket_id: int, fields: dict[str, Any]) -> Envelope:
        # Mutates the backing store, unlike search_tickets/list_comments'
        # canned replies - a caller of update_ticket needs to see its own
        # write reflected on the next get_ticket, the same way the real API
        # would show it. Still deep-copies both in and out (this class's own
        # docstring), so neither the caller's `fields` dict nor the returned
        # envelope alias the fixture's backing store.
        #
        # Checked first, before the existence lookup, matching ApiBackend and
        # assign_ticket below: the refusal does not depend on whether
        # ticket_id is real.
        _refuse_an_empty_update(fields=fields)
        try:
            ticket = self.tickets[ticket_id]
        except KeyError:
            raise exc.NotFound(f"no such record (ticket {ticket_id})") from None
        ticket.update(copy.deepcopy(fields))
        # Important 1 (final whole-branch review): converted for symmetry with
        # ApiBackend, even though this fake's canned shape carries no audit
        # events today - a no-op now, not a promise that stays true if a test
        # fixture ever puts an html_body in the returned ticket.
        return _convert_html_bodies({"ticket": copy.deepcopy(ticket)})

    def assign_ticket(self, *, ticket_id: int, assignee_id: int | None = None, group_id: int | None = None) -> Envelope:
        # Checked first, before the existence lookup below, matching
        # ApiBackend: the refusal does not depend on whether ticket_id is
        # real, so a call naming neither field is refused identically by
        # both backends regardless of the id it was given.
        _refuse_an_empty_assignment(assignee_id=assignee_id, group_id=group_id)
        try:
            ticket = self.tickets[ticket_id]
        except KeyError:
            raise exc.NotFound(f"no such record (ticket {ticket_id})") from None
        if assignee_id is not None:
            ticket["assignee_id"] = assignee_id
        if group_id is not None:
            ticket["group_id"] = group_id
        # Important 1 (final whole-branch review): symmetry with ApiBackend -
        # see update_ticket's fake, just above, for why this is a no-op today.
        return _convert_html_bodies({"ticket": copy.deepcopy(ticket)})

    def add_internal_note(self, *, ticket_id: int, body: str, uploads: list[str] | None = None) -> Envelope:
        # Checked first, before the existence lookup, matching ApiBackend
        # and both siblings above: the refusal does not depend on whether
        # ticket_id is real.
        #
        # Canned, like list_comments: this fake does not maintain a
        # per-ticket comment store, so the returned envelope is the ticket
        # unchanged - there is no comment list here for a caller to read
        # back and confirm is private, the same limitation list_comments
        # already documents for itself.
        _refuse_an_empty_note(body=body, uploads=uploads)
        try:
            ticket = self.tickets[ticket_id]
        except KeyError:
            raise exc.NotFound(f"no such record (ticket {ticket_id})") from None
        # Important 1 (final whole-branch review): symmetry with ApiBackend -
        # see update_ticket's fake, above, for why this is a no-op today.
        return _convert_html_bodies({"ticket": copy.deepcopy(ticket)})

    def solve_ticket(self, *, ticket_id: int) -> Envelope:
        # Mutates the backing store, like update_ticket/assign_ticket: a
        # caller needs to see the ticket read back as solved on a subsequent
        # get_ticket, the same way the real API would show it.
        try:
            ticket = self.tickets[ticket_id]
        except KeyError:
            raise exc.NotFound(f"no such record (ticket {ticket_id})") from None
        ticket["status"] = "solved"
        # Important 1 (final whole-branch review): symmetry with ApiBackend -
        # see update_ticket's fake, above, for why this is a no-op today.
        return _convert_html_bodies({"ticket": copy.deepcopy(ticket)})

    def upload_file(self, *, filename: str, content: bytes, content_type: str) -> Envelope:
        # Canned, like search_tickets/list_comments: an upload is not scoped
        # to any ticket while it is orphaned (that is the whole point of
        # this method - see ApiBackend.upload_file's comment), so there is no
        # self.tickets-shaped store to check it against or record it in.
        # `content_type` is accepted and unused, only to keep this signature
        # identical to ApiBackend's (test_the_two_backends_have_identical_
        # signatures). `content` IS used - it is checked for emptiness below,
        # because a fake that accepted zero bytes would make the empty-upload
        # refusal pass every test while doing nothing in production.
        #
        # Still enforces the same extension refusal ApiBackend does: a fake
        # that let this through would pass tests the real API would refuse.
        _refuse_a_filename_without_extension(filename=filename)
        _refuse_an_empty_upload(content=content)
        # nosec B105 - bandit pattern-matches the KEY name "token" and calls this a
        # hardcoded password. It is a canned return value in a test double, named
        # "fake", never sent anywhere and never compared against a real credential;
        # renaming the value would not help, since the key is what triggers the rule.
        # Suppressed rather than worked around because the finding is false - contrast
        # `auth/_callback.py`, where bandit's B101 was RIGHT (python -O strips asserts)
        # and the code was changed instead of the warning silenced.
        return {"upload": {"token": "fake-upload-token"}}  # nosec B105

    def delete_upload(self, *, token: str) -> Envelope:
        # Canned: no per-upload store exists to check `token` against or
        # remove it from, same reasoning as upload_file above.
        return {}

    def get_attachment(self, *, attachment_id: int) -> Envelope:
        # Canned: no per-attachment store exists to look `attachment_id` up
        # in, same reasoning as upload_file/delete_upload above.
        return {"attachment": {"id": attachment_id}}
