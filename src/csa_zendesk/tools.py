"""The tool surface. ADR-016: a tool is (operation x constrained arguments).

One operation may back several tools, and **the constraint on the request body is
what makes a tool bucket-pure**. `PUT /tickets/{id}` is five impact levels - field
edit, internal note, public reply, solve, close - so it backs five tools, each
refusing the body keys that would change its bucket.

The constraints are enforced, not documented. A tool that merely says it will not
send a public comment is not a control.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import exceptions as exc


@dataclass(frozen=True, slots=True)
class ToolSpec:
    capability: str
    reach: bool = False
    #: Which allowlist governs the object this tool acts on; None for tools that
    #: act on no particular subject (a search, or ticket creation - there is no
    #: existing ticket to scope against yet).
    subject_var: str | None = None
    #: NOTE (fix wave Minor): `_force_public` and `_create_ticket_check` below
    #: mutate the caller's `kwargs["comment"]` dict IN PLACE rather than copying
    #: it. `policy._dispatch` runs `spec.check(kwargs)` before the scope and
    #: reach checks (see that function's docstring for the fixed order), so a
    #: call later refused by scope or reach has already had the caller's own
    #: nested `comment` dict rewritten (e.g. `public` forced to `False`) by the
    #: time the refusal is raised. Harmless today - the call is refused either
    #: way, and no test has needed the pre-check dict back - but worth knowing
    #: before any caller starts reusing a `kwargs` dict across retries.
    check: Callable[[dict[str, Any]], None] = field(default=lambda _kwargs: None)


def _forbid(*keys: str) -> Callable[[dict[str, Any]], None]:
    def check(kwargs: dict[str, Any]) -> None:
        present = [k for k in keys if k in kwargs]
        if present:
            raise exc.PolicyError(
                f"this tool does not accept {', '.join(sorted(present))}. That argument would "
                f"change what the call does and therefore what authority it needs; use the tool "
                f"built for it instead."
            )

    return check


def _only(*keys: str) -> Callable[[dict[str, Any]], None]:
    allowed = set(keys) | {"ticket_id"}

    def check(kwargs: dict[str, Any]) -> None:
        extra = sorted(set(kwargs) - allowed)
        if extra:
            raise exc.PolicyError(
                f"this tool accepts only {sorted(allowed)}; got {extra}. Use the tool built for those fields."
            )

    return check


def _force_public(value: bool) -> Callable[[dict[str, Any]], None]:
    def check(kwargs: dict[str, Any]) -> None:
        _only("comment")(kwargs)
        comment = kwargs.get("comment")
        if not isinstance(comment, dict):
            raise exc.PolicyError("this tool requires a `comment` object")
        # Forced, never merely defaulted: `comment.public` has NO fixed default -
        # it inherits from the ticket's first comment, so an email-originated
        # ticket defaults to PUBLIC. Omitting it here would make the reach of
        # this call depend on the ticket's history.
        comment["public"] = value

    return check


def _status(value: str) -> Callable[[dict[str, Any]], None]:
    def check(kwargs: dict[str, Any]) -> None:
        _only("status")(kwargs)
        if kwargs.get("status") != value:
            raise exc.PolicyError(f"this tool sets status={value!r} only")

    return check


# Forbidden on both create and update (fix wave C3/C4): `custom_status_id` is a
# second route into solve/close alongside the literal `status` key - the OAS's
# own example for updating a ticket pairs `custom_status_id: 321` with
# `status: solved` - and `additional_collaborators`/`email_ccs`/`followers`/
# `collaborator_ids` each notify someone when the ticket changes, which is
# reach from a tool that carries no `reach=True` flag. Forbidding these named
# keys does not make either tool bucket-pure the way an allowlist would (root
# finding: a denylist over a body the tool never enumerates is unclosable) -
# it closes the four known routes without claiming to close routes the OAS has
# not shown us yet. See `specs/zendesk-support-oas.yaml`'s `UpdateTicket` and
# `Ticket` schema examples for the exact fields.
_TICKET_REACH_SIDE_DOORS = (
    "custom_status_id",
    "additional_collaborators",
    "email_ccs",
    "followers",
    "collaborator_ids",
)


def _create_ticket_check(kwargs: dict[str, Any]) -> None:
    # Both halves of the CSV constraint ("no public comment; body must not
    # contain status, custom_status_id, or the collaborator fields"): `status`
    # and the reach side doors are refused outright - creating and solving, or
    # creating and notifying a collaborator, in one call would span two impact
    # buckets - and any `comment` supplied with the new ticket is forced
    # private, because a public one would email the requester (reach) from a
    # tool that carries no `reach=True` flag.
    _forbid("status", *_TICKET_REACH_SIDE_DOORS)(kwargs)
    comment = kwargs.get("comment")
    if isinstance(comment, dict):
        comment["public"] = False


TOOLS: dict[str, ToolSpec] = {
    "get_ticket": ToolSpec("ticket.read", subject_var="CSA_ZD_ALLOWLIST_READ"),
    "search_tickets": ToolSpec("ticket.read"),
    # Important 4 (final whole-branch review): `list_comments` had a
    # `policy._GATES` entry (so `PolicyBackend` gates it on `ticket.read`) but
    # NO entry here at all, so `assert_subject_permitted`'s `spec =
    # tools.TOOLS.get(tool)` returned `None` and the read allowlist was
    # decorative for this one tool - an operator setting
    # `CSA_ZD_ALLOWLIST_READ=44821,44822` found `get_ticket(99999)` refused
    # and `list_comments(99999)` returning the whole conversation anyway.
    # Scoped by the same allowlist as `get_ticket`, since both act on a ticket
    # named by `ticket_id`. See `test_every_gated_backend_method_has_a_tool_spec`
    # in `tests/test_tools.py` for the cross-check that now catches a repeat.
    "list_comments": ToolSpec("ticket.read", subject_var="CSA_ZD_ALLOWLIST_READ"),
    "create_ticket": ToolSpec("ticket.write", check=_create_ticket_check),
    "update_ticket": ToolSpec(
        "ticket.write",
        subject_var="CSA_ZD_ALLOWLIST_WRITE",
        check=_forbid("comment", "status", *_TICKET_REACH_SIDE_DOORS),
    ),
    "assign_ticket": ToolSpec(
        "ticket.write", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_only("assignee_id", "group_id")
    ),
    "add_internal_note": ToolSpec("ticket.note", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_force_public(False)),
    "reply_publicly": ToolSpec(
        "ticket.reply", reach=True, subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_force_public(True)
    ),
    "solve_ticket": ToolSpec("ticket.solve", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_status("solved")),
    "close_ticket": ToolSpec("ticket.close", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_status("closed")),
    # ticket.merge, not ticket.close (fix wave C1): POST .../merge accepts
    # source_comment_is_public/target_comment_is_public, the same reach mechanism
    # as a public reply, tabled `internal` and unguarded until this fix. Forbidding
    # both keys is a denylist over an endpoint whose body this table does not
    # enumerate - it closes the two known keys, not the shape (root finding) - so
    # reach=True stands regardless of the constraint, as the defense the
    # constraint alone cannot promise.
    "merge_tickets": ToolSpec(
        "ticket.merge",
        reach=True,
        subject_var="CSA_ZD_ALLOWLIST_WRITE",
        check=_forbid("source_comment_is_public", "target_comment_is_public"),
    ),
    "update_trigger": ToolSpec("admin.write", subject_var="CSA_ZD_ALLOWLIST_ADMIN"),
}
