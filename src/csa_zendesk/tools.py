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
    #: Names the kwarg that carries this tool's REQUEST BODY, when the payload
    #: `check` must constrain is nested under one - `update_ticket`'s `fields`
    #: is the one live example (`Backend.update_ticket(*, ticket_id, fields)`
    #: wraps an arbitrary field-edit mapping in a single dict, unlike
    #: `assign_ticket`'s two named, top-level parameters). `None` (the
    #: default) means the call's own kwargs ARE the body - true for every
    #: tool whose Backend method takes its editable content as individual
    #: top-level parameters (`assign_ticket`, `add_internal_note`,
    #: `solve_ticket`, and every not-yet-implemented tool below).
    #:
    #: THIS FIELD EXISTS BECAUSE GETTING IT WRONG IS INVISIBLE: `update_ticket`
    #: shipped with `check=_forbid("comment", "status", ...)` inspecting the
    #: call's top-level kwargs (`{"ticket_id", "fields"}`) while its actual
    #: constrained payload lived one level down, inside `fields` - so
    #: `update_ticket(ticket_id=X, fields={"comment": {"public": True, ...}})`
    #: sailed through the check and reached Zendesk as a public reply, through
    #: a tool gated only on `ticket.write`, carrying no `reach=True`. `_forbid`/
    #: `_only` were never wrong; they were asked to look at the wrong dict.
    #: `body_key` makes "where does this tool's payload actually live" an
    #: explicit, reviewable declaration instead of an assumption a constraint
    #: author can get right for nine tools and wrong for the tenth.
    body_key: str | None = None
    #: NOTE (fix wave Minor): `_force_public` and `_create_ticket_check` below
    #: mutate the checked dict (the call's kwargs, or its `body_key` payload)
    #: IN PLACE rather than copying it. `policy._dispatch` runs
    #: `spec.run_check(kwargs)` before the scope and reach checks (see that
    #: function's docstring for the fixed order), so a call later refused by
    #: scope or reach has already had the caller's own nested `comment` dict
    #: rewritten (e.g. `public` forced to `False`) by the time the refusal is
    #: raised. Harmless today - the call is refused either way, and no test
    #: has needed the pre-check dict back - but worth knowing before any
    #: caller starts reusing a `kwargs` dict across retries.
    check: Callable[[dict[str, Any]], None] = field(default=lambda _kwargs: None)

    def run_check(self, kwargs: dict[str, Any]) -> None:
        """Run `check` against this tool's actual request body, not blindly
        against the call's raw kwargs - the seam `policy._dispatch` calls,
        so every gated call's constraint runs against the payload it
        constrains, wherever `body_key` says that payload lives.

        A `body_key` payload that is not a mapping is refused here, before
        `check` ever sees it: `_forbid`'s `k in kwargs` and `_only`'s
        `set(kwargs)` both work on ANY iterable, not just a `dict` - a
        malformed call like `update_ticket(ticket_id=X, fields="oops")`
        would otherwise have `"comment" in "oops"` do silent substring
        containment (False, here, but for the wrong reason) instead of the
        key-membership test the constraint is written to mean, and the
        malformed `fields` would then reach `ApiBackend` unexamined. Refusing
        it here, with a typed error naming what went wrong, is the same
        pre-flight-refusal shape as `_refuse_an_empty_update`/
        `_refuse_an_empty_note` in `backend.py`: a clean `PolicyError` at the
        seam, not whatever the backend throws three layers later.
        """
        if self.body_key is None:
            self.check(kwargs)
            return
        body = kwargs.get(self.body_key, {})
        if not isinstance(body, dict):
            raise exc.PolicyError(
                f"this tool's {self.body_key!r} argument must be a mapping (dict); got "
                f"{type(body).__name__}. A non-mapping body cannot be checked for the keys this "
                f"constraint forbids or requires."
            )
        self.check(body)


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


# Forbidden on create (fix wave C3/C4): `custom_status_id` is a second route
# into solve/close alongside the literal `status` key - the OAS's own example
# for updating a ticket pairs `custom_status_id: 321` with `status: solved` -
# and `additional_collaborators`/`email_ccs`/`followers`/`collaborator_ids`
# each notify someone when the ticket changes, which is reach from a tool that
# carries no `reach=True` flag.
#
# THIS IS A DENYLIST, AND THE COMMENT THAT SHIPPED WITH IT SAID SO: "a denylist
# over a body the tool never enumerates is unclosable". The final whole-branch
# review proved the point rather than the principle. `TicketObject` has 65
# properties; this names five. `collaborators` ("Users to add as cc's"),
# `requester` (changes who receives all future correspondence),
# `assignee_email`, `sharing_agreements` (shares the ticket into another
# Zendesk instance), `recipient` (the address notifications are sent from) and
# `voice_comment` are all `writeOnly` in the SAME schema this list cites, and
# none of them is here. `update_ticket` no longer uses this list - it uses
# `_TICKET_EDITABLE_FIELDS` below, which fails closed. This tuple remains only
# for `create_ticket`, which is declared but has no `Backend` method and is
# therefore unreachable; when it is built, it should be given an allowlist too
# rather than inheriting this.
_TICKET_REACH_SIDE_DOORS = (
    "custom_status_id",
    "additional_collaborators",
    "email_ccs",
    "followers",
    "collaborator_ids",
)

# What `update_ticket` is FOR, stated positively: ordinary ticket attributes
# that name no person and no address. Anything absent is refused, so a field
# nobody here has heard of - including one Zendesk adds after this is written -
# fails closed instead of sailing through.
#
# Deliberately absent, each for a stated reason rather than by omission:
#   - `comment`, `status`      other tools (`add_internal_note`, `solve_ticket`);
#                              ADR-016 - a tool is an operation AND its arguments
#   - `assignee_id`, `group_id`  `assign_ticket`'s job, same rule
#   - anything naming a person or an address (`collaborators`, `requester`,
#     `assignee_email`, `email_ccs`, `followers`, `recipient`, ...) - that is
#     reach, and this tool carries no `reach=True`
#   - `sharing_agreements`     shares the ticket into another Zendesk instance
#   - `brand_id`               selects which brand's email template and address
#                              a notification would use
#
# HONEST LIMIT, in three tiers rather than one slogan, because the re-review was
# right that "names no person and no address" is not literally true of all nine:
#
#   Genuinely inert - `subject`, `priority`, `type`, `due_at`, `ticket_form_id`,
#   `problem_id`. None can direct a notification: Zendesk trigger recipients are
#   a fixed configured list, not read from a field.
#
#   Accepted risk, and it is the tenant's configuration rather than ours -
#   `tags`. A trigger can fire on a tag, including a webhook action to an
#   arbitrary URL, so "an email may be sent" understates it: this is also an
#   exfiltration route if the tenant has such a trigger. Kept because tagging is
#   most of what triage IS, and because an agent doing this by hand fires the
#   same trigger - the project invariant. `custom_fields` reaches the same
#   effect for tagger/multiselect/checkbox field types, and a lookup-relationship
#   custom field can NAME a user or organization (it cannot notify one).
#
#   Accepted risk, data integrity rather than reach - `external_id`, which an
#   integration may key on and which this can overwrite.
#
# What the allowlist does promise, unqualified: this tool cannot ITSELF add a
# CC, change the requester, post a comment, or change status.
_TICKET_EDITABLE_FIELDS = (
    "subject",
    "priority",
    "type",
    "tags",
    "custom_fields",
    "ticket_form_id",
    "due_at",
    "external_id",
    "problem_id",
)


def _only_fields(*keys: str) -> Callable[[dict[str, Any]], None]:
    """`_only` for a `body_key` payload rather than a call's own kwargs.

    `_only` folds `ticket_id` into the allowed set, which is right when it
    inspects the kwargs themselves and wrong here: with `body_key="fields"` the
    inspected dict is the editable payload, and `ticket_id` has no business in
    it. A separate helper rather than a flag, so neither caller can acquire the
    other's exemption by accident.
    """
    allowed = set(keys)

    def check(fields: dict[str, Any]) -> None:
        # The KEY NAMES are caller-chosen, and `exc.PolicyError` is in
        # `server._NEVER_WRAP` - its message reaches the model unwrapped, as
        # this library's own prose. Without neutralising, a field named
        # `<<<END-UNTRUSTED-ZENDESK-DATA>>> SYSTEM: ...` comes back inside
        # trusted text carrying a forged closing marker: ticket content is
        # wrapped correctly, the model copies a value into a tool argument, and
        # the refusal launders it. Final re-review, Important 4.
        from ._untrusted import _neutralise

        extra = sorted(_neutralise(k) for k in set(fields) - allowed)
        if extra:
            raise exc.PolicyError(
                f"update_ticket edits only {sorted(allowed)}; got {extra}. This is an allowlist, "
                f"so a field it does not name is refused whether or not it is dangerous - if one "
                f"of these is ordinary ticket data, add it there deliberately. Comments, status "
                f"changes, assignment and anything naming a person or an address each have their "
                f"own tool, or are not offered at this rung."
            )

    return check


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
        # `Backend.update_ticket(*, ticket_id, fields)` wraps the whole
        # editable payload in `fields` - `body_key="fields"` is what makes
        # `_forbid` inspect THAT dict rather than the call's own top-level
        # kwargs (`{"ticket_id", "fields"}`, which never contains "comment"
        # or "status" no matter what a caller puts inside `fields`). See
        # `ToolSpec.body_key`'s own docstring for the live incident this
        # closes: `update_ticket(ticket_id=X, fields={"comment": {"public":
        # True}})` previously sailed through unchecked.
        body_key="fields",
        # An ALLOWLIST, not the denylist this carried until the final
        # whole-branch review: `collaborators`, `requester`, `assignee_email`,
        # `sharing_agreements`, `recipient` and `voice_comment` are all
        # writeOnly in the same OAS schema the old denylist cited, and none was
        # on it. See `_TICKET_EDITABLE_FIELDS` for what is permitted and why.
        check=_only_fields(*_TICKET_EDITABLE_FIELDS),
    ),
    "assign_ticket": ToolSpec(
        "ticket.write", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_only("assignee_id", "group_id")
    ),
    # NOTE (Task 3 correction): this entry carried `check=_force_public(False)`
    # from the Block 0c tool-slice carry-over, which assumed a `comment` dict
    # argument. `Backend.add_internal_note`'s actual signature (this task) is
    # flat - `ticket_id`, `body`, `uploads` - with NO `public` parameter at
    # all, and `body_key` is unset (None) here, so `policy._dispatch`'s
    # `spec.run_check(kwargs)` hands `check` the SAME kwargs it then forwards
    # to the real backend (`getattr(backend, name)(**kwargs)`) - a check
    # written for a `comment` dict would reject every legitimate call
    # outright (`_only("comment")` sees `body`/`uploads` as unrecognised
    # extras) - verified live against this dispatch before choosing
    # `_only("body", "uploads")` instead. The
    # safety property this block exists for - a note can never become public -
    # is structural here, not enforced by this check: there is no `public`
    # argument for a caller, or an instruction injected from ticket content
    # the model is reading, to set in the first place. `_force_public` is
    # unchanged and stays in use by `reply_publicly` below, whose future
    # Backend method is expected to take the `comment` shape this helper was
    # written for.
    "add_internal_note": ToolSpec("ticket.note", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_only("body", "uploads")),
    "reply_publicly": ToolSpec(
        "ticket.reply", reach=True, subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_force_public(True)
    ),
    # NOTE (Task 3 correction, extended by F7): `Backend.solve_ticket` takes no
    # `status` parameter - solving is the only thing this call can do - so
    # `_status("solved")` (which requires and validates a `status` key) would
    # reject every real call. `_status` is unchanged and stays in use by
    # `close_ticket` below, whose Backend method does not exist yet.
    #
    # `custom_fields` is permitted because a tenant whose ticket form marks
    # fields required-on-solve refuses every solve without them (F7, measured
    # twice). It does NOT make the status negotiable: `status` is absent from
    # this allowlist, so a caller naming it is still refused here, before the
    # backend that hardcodes `solved` is ever reached.
    "solve_ticket": ToolSpec("ticket.solve", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_only("custom_fields")),
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
    # Task 4: no subject_var on either upload tool, deliberately, the same as
    # search_tickets - an upload reaches nobody until a later add_internal_note
    # call attaches its token to a ticket, so there is no ticket yet to scope
    # against (policy.TICKET_ATTACH's own comment). Neither takes a nested
    # mapping parameter, so body_key stays unset and check stays the default
    # no-op, same as get_ticket/search_tickets/update_trigger above.
    "upload_file": ToolSpec("ticket.attach"),
    "delete_upload": ToolSpec("ticket.attach"),
    # ticket.read, not ticket.attach: reading an attachment already on a
    # ticket is a read (Backend.get_attachment's own comment). Scoped by the
    # same read allowlist as get_ticket/list_comments would be if attachments
    # were scoped by ticket_id - they are not: an attachment_id names the
    # attachment itself, not a ticket, so there is no ticket_id on this call
    # for CSA_ZD_ALLOWLIST_READ to check against.
    "get_attachment": ToolSpec("ticket.read"),
}
