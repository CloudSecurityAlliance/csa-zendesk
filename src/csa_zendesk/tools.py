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


def _create_ticket_check(kwargs: dict[str, Any]) -> None:
    # Both halves of the CSV constraint ("no public comment; body must not
    # contain status"): `status` is refused outright - creating and solving in
    # one call would span two impact buckets - and any `comment` supplied with
    # the new ticket is forced private, because a public one would email the
    # requester (reach) from a tool that carries no `reach=True` flag.
    _forbid("status")(kwargs)
    comment = kwargs.get("comment")
    if isinstance(comment, dict):
        comment["public"] = False


TOOLS: dict[str, ToolSpec] = {
    "get_ticket": ToolSpec("ticket.read", subject_var="CSA_ZD_ALLOWLIST_READ"),
    "search_tickets": ToolSpec("ticket.read"),
    "create_ticket": ToolSpec("ticket.write", check=_create_ticket_check),
    "update_ticket": ToolSpec("ticket.write", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_forbid("comment", "status")),
    "assign_ticket": ToolSpec(
        "ticket.write", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_only("assignee_id", "group_id")
    ),
    "add_internal_note": ToolSpec("ticket.note", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_force_public(False)),
    "reply_publicly": ToolSpec(
        "ticket.reply", reach=True, subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_force_public(True)
    ),
    "solve_ticket": ToolSpec("ticket.solve", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_status("solved")),
    "close_ticket": ToolSpec("ticket.close", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_status("closed")),
    "merge_tickets": ToolSpec("ticket.close", subject_var="CSA_ZD_ALLOWLIST_WRITE"),
    "update_trigger": ToolSpec("admin.write", subject_var="CSA_ZD_ALLOWLIST_ADMIN"),
}
