import collections.abc
import types
import typing

import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk import tools


def _hint_is_nested_mapping(hint: object) -> bool:
    """Whether a resolved type hint denotes a nested-mapping payload (a
    Backend method's `fields`-style parameter), however it is spelled -
    `dict[str, Any]`, `dict[str, Any] | None`, `Mapping[str, Any]`,
    `MutableMapping[str, Any]`, ... - as opposed to a scalar (`int`, `str`,
    `list[str] | None`, ...).

    Test-only classifier for `test_body_key_matches_the_real_backend_
    signature_for_every_live_constrained_tool` below - not production code,
    since nothing at runtime needs to classify a type hint; `ToolSpec.
    body_key` is a plain declared string, and this function exists only to
    check that declaration against a Backend method's real signature.

    STRUCTURAL, not an enumerated name list: a union is unwrapped by
    stripping `NoneType` and recursing on what remains (an Optional nested
    mapping is still a nested mapping) - more than one non-None arm is
    ambiguous and RAISES, the same "a human must decide" posture the caller
    takes for two nested parameters on one method, rather than guessing
    which arm is the real payload. Once unwrapped, the test is
    `issubclass(origin, collections.abc.Mapping)` - not
    `origin in (dict, Mapping, MutableMapping, ...)` - because `dict` is
    already a registered subclass of `collections.abc.Mapping` (verified:
    `typing.get_origin(dict[str, Any])` is `dict` itself, and
    `issubclass(dict, collections.abc.Mapping)` is `True`), and
    `typing.Mapping[...]`/`typing.MutableMapping[...]` both normalise their
    origin to the `collections.abc` class of the same name at runtime
    (verified live) - so one subclass check recognises the whole
    Mapping/MutableMapping family, including one this codebase does not use
    yet, without this function needing to learn a new name first.

    Found empirically, and the reason this function exists rather than the
    one-line `typing.get_origin(hint) is dict` the first version of this
    guard used: that one-liner silently classifies `dict[str, Any] | None`
    and `Mapping[str, Any]` as NOT nested (`get_origin` returns
    `types.UnionType`/`collections.abc.Mapping`, neither of which `is dict`),
    so `expected` falls back to `None` - and an author who correctly sets
    `body_key="fields"` on such a method gets a FAILING guard (safe: it
    forces a human look), while an author who leaves `body_key=None` (the
    update_ticket defect, exactly) gets a PASSING one, because `None ==
    None`. The miss failed in the unsafe direction for the one spelling
    (`X | None`) a real public-reply body is likely to use, and for the one
    spelling (`Mapping[...]`) that is the MORE correct annotation for a
    parameter a method only reads.
    """
    origin = typing.get_origin(hint)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [arg for arg in typing.get_args(hint) if arg is not type(None)]
        if len(non_none) != 1:
            raise ValueError(
                f"{hint!r} is a union with more than one non-None arm - which arm (if any) is the "
                f"nested body is not something this classifier can decide; a human must."
            )
        return _hint_is_nested_mapping(non_none[0])
    return isinstance(origin, type) and issubclass(origin, collections.abc.Mapping)


@pytest.mark.parametrize(
    "hint,expected",
    [
        (dict[str, typing.Any], True),
        (dict[str, typing.Any] | None, True),
        (typing.Mapping[str, typing.Any], True),
        (typing.MutableMapping[str, typing.Any], True),
        (int, False),
        (str, False),
        (int | None, False),
        (list[str] | None, False),
    ],
)
def test_hint_is_nested_mapping_classifies_every_spelling_this_codebase_uses(hint, expected):
    # The failure is in classification, not in the loop that uses it - so
    # this proves the classifier itself is right for each spelling, rather
    # than only proving the guard test below happens to pass today.
    assert _hint_is_nested_mapping(hint) is expected


def test_hint_is_nested_mapping_refuses_to_guess_at_a_genuinely_ambiguous_union():
    with pytest.raises(ValueError, match="human"):
        _hint_is_nested_mapping(int | str)


def test_every_tool_in_the_table_exists_in_code():
    import csv
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    with (root / "analysis/tool-boundaries.csv").open() as fh:
        named = {r["tool"] for r in csv.DictReader(fh)}
    assert named == set(tools.TOOLS), named ^ set(tools.TOOLS)


def test_every_gated_backend_method_has_a_tool_spec():
    # Important 4 (final whole-branch review): `list_comments` had a
    # `policy._GATES` entry (so `PolicyBackend` gates the call on
    # `ticket.read`) but no `tools.TOOLS` entry at all, so
    # `policy.assert_subject_permitted`'s `spec = tools.TOOLS.get(tool)`
    # returned `None` and the read allowlist was silently decorative for that
    # one tool - nothing cross-checked `policy._GATES` against `tools.TOOLS`
    # to catch it. The direction that matters is `_GATES` -> `TOOLS`, not the
    # reverse: `tools.TOOLS` legitimately carries write-tool entries
    # (`create_ticket`, `update_ticket`, ...) with no `Backend` method or
    # `_GATES` entry behind them yet, since this table also serves later
    # blocks' surface - but every name `_GATES` actually gates must have a
    # `ToolSpec`, or a scope control written for one tool is quietly
    # unreachable for a sibling sharing its capability and its Backend
    # method. No exemptions exist today; if one is ever needed, name it here
    # explicitly rather than letting this assertion go silently stale.
    from csa_zendesk import policy

    ungoverned_by_tools = set(policy._GATES) - set(tools.TOOLS)
    assert not ungoverned_by_tools, (
        f"Backend method(s) gated by policy._GATES but with no tools.TOOLS entry - any "
        f"subject_var scoping written for a sibling tool never reaches these: {ungoverned_by_tools}"
    )


def test_body_key_matches_the_real_backend_signature_for_every_live_constrained_tool():
    # THE GUARD for the update_ticket incident, not just its regression test:
    # a ToolSpec whose `check` is not the default no-op, and whose Backend
    # method already exists, is introspected against that method's REAL
    # signature - `body_key` must name the one parameter holding a nested
    # mapping, or stay None when every parameter (besides `ticket_id`) is
    # scalar. Without this, `run_check` silently degenerates to
    # `check(kwargs)` for any tool that leaves `body_key=None` - correct for
    # `assign_ticket`, and exactly the update_ticket bug for a tool that
    # actually nests its payload - and nothing distinguishes the two short of
    # a human reading both the ToolSpec and the Backend method side by side.
    #
    # Skipped by ABSENCE of a Backend method, not a hand-written tool-name
    # list: `create_ticket`, `reply_publicly`, `close_ticket` and
    # `merge_tickets` carry a real constraint today but no Backend method -
    # a literal exemption list would stay silent exactly when one of those
    # four is implemented, which is the moment this guard is supposed to
    # start working for it. `hasattr(Backend, name)` is the absence test:
    # true for every name the Protocol actually declares, false for a
    # tools.TOOLS entry that is still speculative.
    #
    # Nested-ness is classified by `_hint_is_nested_mapping` (module-level,
    # above), not a bare `typing.get_origin(hint) is dict` check - that
    # one-liner was the guard's own first version, and it silently missed
    # `dict[str, Any] | None` and `Mapping[str, Any]` (see that function's
    # docstring for the live-verified failure mode: an author who correctly
    # set `body_key` on such a method would have FAILED this guard; one who
    # left `body_key=None` - the update_ticket defect itself - would have
    # PASSED it).
    from csa_zendesk.backend import Backend

    default_check = tools.ToolSpec.__dataclass_fields__["check"].default
    checked_any = False
    for name, spec in tools.TOOLS.items():
        if spec.check is default_check:
            continue  # no constraint declared - nothing to verify body_key against
        if not hasattr(Backend, name):
            continue  # not implemented yet - see docstring
        checked_any = True
        hints = typing.get_type_hints(getattr(Backend, name))
        nested = [
            pname
            for pname, hint in hints.items()
            if pname not in ("ticket_id", "return") and _hint_is_nested_mapping(hint)
        ]
        assert len(nested) <= 1, (
            f"{name}: Backend.{name} takes more than one nested-mapping parameter {nested} - "
            f"body_key cannot name a single one; this needs a human decision, not this guard's."
        )
        expected = nested[0] if nested else None
        assert spec.body_key == expected, (
            f"{name}: Backend.{name}'s real signature says its constrained payload lives at "
            f"body_key={expected!r}, but tools.TOOLS[{name!r}].body_key is {spec.body_key!r} - "
            f"`check` would run against the wrong dict here, the exact update_ticket incident."
        )
    # A vacuous loop (every branch `continue`s) would pass by construction and
    # prove nothing - guard the guard itself.
    assert checked_any, "no live, constrained tool was found to check - this guard has gone vacuous"


def test_the_csv_and_tools_table_agree_on_which_tools_reach():
    # Fix wave item 1: `test_every_tool_in_the_table_exists_in_code` above
    # compares tool NAMES only - it would stay green if the table said
    # `merge_tickets` was internal while ToolSpec said `reach=True` (or vice
    # versa). A corrected table with uncorrected code (or the reverse) is
    # exactly the "documented but not enforced" failure ADR-016 exists to
    # prevent, so this compares the reach axis itself, per tool.
    import csv
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    with (root / "analysis/tool-boundaries.csv").open() as fh:
        rows = {r["tool"]: r for r in csv.DictReader(fh)}
    for name, spec in tools.TOOLS.items():
        csv_reach = rows[name]["reach"] == "contacts-a-person"
        assert spec.reach == csv_reach, (
            f"{name}: CSV says reach={rows[name]['reach']!r} but ToolSpec.reach={spec.reach!r}"
        )


def test_update_ticket_refuses_a_comment():
    # Unit test of the underlying _forbid callable in isolation - not the
    # shape update_ticket is actually called with (see the run_check tests
    # below for that). The whole of ADR-016 in one assertion: the constraint
    # is the control. A tool that merely documents "I will not comment" is
    # not a control.
    with pytest.raises(exc.PolicyError, match="comment"):
        tools.TOOLS["update_ticket"].check({"ticket_id": 1, "comment": {"body": "hi"}})


def test_update_ticket_refuses_a_status():
    with pytest.raises(exc.PolicyError, match="status"):
        tools.TOOLS["update_ticket"].check({"ticket_id": 1, "status": "solved"})


def test_update_ticket_permits_a_field_edit():
    tools.TOOLS["update_ticket"].check({"ticket_id": 1, "priority": "high"})


# --- regression: update_ticket's constraint must inspect `fields`, the level
# --- its payload actually arrives at, not the call's own top-level kwargs.
# --- `Backend.update_ticket(*, ticket_id, fields)` wraps its editable content
# --- in `fields`; `policy._dispatch` calls `spec.run_check(kwargs)` with
# --- `kwargs == {"ticket_id": ..., "fields": {...}}`, so a check that only
# --- ever looked at `kwargs` itself would see "comment"/"status" nested
# --- inside `fields` as nothing at all. This is the exact bypass:
# --- `update_ticket(ticket_id=159143, fields={"comment": {"public": True}})`
# --- would otherwise reach Zendesk as a public reply through a tool gated
# --- only on ticket.write, carrying no reach=True.


def test_update_ticket_body_key_names_fields():
    assert tools.TOOLS["update_ticket"].body_key == "fields"


def test_update_ticket_run_check_refuses_a_comment_nested_in_fields():
    with pytest.raises(exc.PolicyError, match="comment"):
        tools.TOOLS["update_ticket"].run_check({"ticket_id": 1, "fields": {"comment": {"body": "hi", "public": True}}})


def test_update_ticket_run_check_refuses_a_status_nested_in_fields():
    with pytest.raises(exc.PolicyError, match="status"):
        tools.TOOLS["update_ticket"].run_check({"ticket_id": 1, "fields": {"status": "solved"}})


@pytest.mark.parametrize(
    "key,value",
    [
        ("custom_status_id", 321),
        ("additional_collaborators", ["a@example.com"]),
        ("email_ccs", [{"user_email": "a@example.com", "action": "put"}]),
        ("followers", [{"user_email": "a@example.com", "action": "put"}]),
        ("collaborator_ids", [123]),
    ],
)
def test_update_ticket_run_check_refuses_each_reach_side_door_nested_in_fields(key, value):
    # These four exist precisely because they are side doors to reach - a
    # bypass around the nesting is the same defect wearing a different name.
    with pytest.raises(exc.PolicyError, match=key):
        tools.TOOLS["update_ticket"].run_check({"ticket_id": 1, "fields": {key: value}})


def test_update_ticket_run_check_permits_an_ordinary_field_edit():
    tools.TOOLS["update_ticket"].run_check({"ticket_id": 1, "fields": {"priority": "high"}})


def test_run_check_is_a_no_op_wrapper_when_body_key_is_unset():
    # Every other live tool (assign_ticket, add_internal_note, solve_ticket)
    # takes its editable content as top-level kwargs, so its body_key is
    # None and run_check must behave exactly like calling check(kwargs)
    # directly - proven here rather than assumed.
    assert tools.TOOLS["assign_ticket"].body_key is None
    tools.TOOLS["assign_ticket"].run_check({"ticket_id": 1, "assignee_id": 7})
    with pytest.raises(exc.PolicyError, match="priority"):
        tools.TOOLS["assign_ticket"].run_check({"ticket_id": 1, "priority": "high"})


@pytest.mark.parametrize("bogus_fields", ["oops", ["comment"], 1, None])
def test_run_check_refuses_a_non_mapping_body_key_payload(bogus_fields):
    # Re-review finding: _forbid's `k in kwargs` and _only's `set(kwargs)`
    # both work on ANY iterable, not just a dict - "comment" in "oops" is a
    # silent, meaningless substring test rather than the key-membership test
    # the constraint means, and a non-iterable body (1, None) would raise an
    # unrelated TypeError instead of a clean refusal. update_ticket(ticket_id=X,
    # fields="oops") must be refused here, before `check` ever runs against it.
    with pytest.raises(exc.PolicyError, match="mapping"):
        tools.TOOLS["update_ticket"].run_check({"ticket_id": 1, "fields": bogus_fields})


def test_run_check_permits_a_missing_body_key_payload_as_empty():
    # A body_key naming a key absent from kwargs entirely reads as {} (an
    # empty mapping), not a malformed one - update_ticket's `fields` is a
    # required Backend parameter, so this only arises from a raw dispatch
    # call missing it, which the final backend call itself will refuse with
    # its own TypeError; run_check does not need to anticipate that here.
    tools.TOOLS["update_ticket"].run_check({"ticket_id": 1})


def test_add_internal_note_permits_only_body_and_uploads():
    # THE control this block exists to get right (API-SURFACE §5.4f):
    # Backend.add_internal_note has no `public` parameter at all - there is
    # nothing to force here, because there is nothing a caller (or an
    # instruction injected from ticket content) could set in the first
    # place. The allowlist instead refuses an attempt to smuggle one in as
    # an extra kwarg, with a clean PolicyError rather than a raw TypeError
    # three frames later inside ApiBackend.
    tools.TOOLS["add_internal_note"].check({"ticket_id": 1, "body": "note", "uploads": ["tok1"]})
    tools.TOOLS["add_internal_note"].check({"ticket_id": 1, "body": "note"})
    with pytest.raises(exc.PolicyError, match="public"):
        tools.TOOLS["add_internal_note"].check({"ticket_id": 1, "body": "note", "public": True})


def test_reply_publicly_forces_public_true_and_is_flagged_for_reach():
    kwargs = {"ticket_id": 1, "comment": {"body": "hello"}}
    tools.TOOLS["reply_publicly"].check(kwargs)
    assert kwargs["comment"]["public"] is True
    assert tools.TOOLS["reply_publicly"].reach is True


def test_reply_publicly_and_merge_tickets_reach_a_person():
    # Fix wave C1: merge_tickets joined reply_publicly here - POST .../merge
    # accepts source_comment_is_public/target_comment_is_public, the same reach
    # mechanism as a public reply. A test that only ever names reply_publicly
    # would stay green even if a second reach-carrying tool went undeclared.
    reaching = {n for n, t in tools.TOOLS.items() if t.reach}
    assert reaching == {"reply_publicly", "merge_tickets"}


def test_merge_is_gated_at_a_terminal_reach_capability_of_its_own():
    # TODO C6: merging closes the source ticket - it is not ticket.write. Fix
    # wave C1: it is not ticket.close either, because close_ticket can carry no
    # comment at all and merge_tickets can, so folding merge into ticket.close
    # would either wrongly flag close_ticket as reach or wrongly leave
    # merge_tickets un-flagged. ticket.merge is its own capability for exactly
    # that reason.
    assert tools.TOOLS["merge_tickets"].capability == "ticket.merge"


def test_assign_ticket_permits_only_assignment_fields():
    tools.TOOLS["assign_ticket"].check({"ticket_id": 1, "assignee_id": 7})
    with pytest.raises(exc.PolicyError, match="priority"):
        tools.TOOLS["assign_ticket"].check({"ticket_id": 1, "priority": "high"})


# --- carried requirement 4: create_ticket needs BOTH halves of its constraint --


def test_create_ticket_forbids_status():
    with pytest.raises(exc.PolicyError, match="status"):
        tools.TOOLS["create_ticket"].check({"subject": "help", "status": "solved"})


def test_create_ticket_forces_any_comment_private():
    # "No public comment" is a forced default, not a refusal: creating a ticket
    # normally carries the requester's initial description as a comment, and the
    # tool must not let that comment be public - solve-and-reply-in-one-call
    # would otherwise span two impact buckets (write + reach) in a single tool.
    kwargs = {"subject": "help", "comment": {"body": "please help", "public": True}}
    tools.TOOLS["create_ticket"].check(kwargs)
    assert kwargs["comment"]["public"] is False


def test_create_ticket_permits_no_comment_at_all():
    tools.TOOLS["create_ticket"].check({"subject": "help"})


# --- coverage: the remaining check() branches ---------------------------------


def test_reply_publicly_requires_a_comment_object():
    with pytest.raises(exc.PolicyError, match="comment"):
        tools.TOOLS["reply_publicly"].check({"ticket_id": 1})


def test_solve_ticket_permits_only_ticket_id():
    # Backend.solve_ticket(*, ticket_id: int) has no `status` parameter -
    # solving is the only thing this call can do, by construction
    # (ApiBackend always sends status="solved"; there is no caller-reachable
    # channel to send anything else). The allowlist still refuses an extra
    # kwarg cleanly rather than letting it reach ApiBackend as a raw
    # TypeError.
    tools.TOOLS["solve_ticket"].check({"ticket_id": 1})
    with pytest.raises(exc.PolicyError, match="status"):
        tools.TOOLS["solve_ticket"].check({"ticket_id": 1, "status": "closed"})


def test_close_ticket_sets_status_closed_only():
    tools.TOOLS["close_ticket"].check({"ticket_id": 1, "status": "closed"})
    with pytest.raises(exc.PolicyError, match="closed"):
        tools.TOOLS["close_ticket"].check({"ticket_id": 1, "status": "solved"})


def test_get_ticket_and_search_tickets_and_update_trigger_have_no_op_checks():
    # These three use ToolSpec's default no-op check - there is no constraint to
    # enforce beyond capability, scope and (where relevant) reach. merge_tickets
    # used to be a fourth (fix wave C1 gave it a real check; see below).
    tools.TOOLS["get_ticket"].check({"ticket_id": 1})
    tools.TOOLS["search_tickets"].check({"query": "status:open"})
    tools.TOOLS["update_trigger"].check({"trigger_id": 1})


# --- fix wave C1: merge_tickets forbids the two public-comment flags -----------


def test_merge_tickets_permits_an_ordinary_merge():
    tools.TOOLS["merge_tickets"].check({"ticket_id": 1, "ids": [2]})


def test_merge_tickets_forbids_source_comment_is_public():
    with pytest.raises(exc.PolicyError, match="source_comment_is_public"):
        tools.TOOLS["merge_tickets"].check({"ticket_id": 1, "ids": [2], "source_comment_is_public": True})


def test_merge_tickets_forbids_target_comment_is_public():
    with pytest.raises(exc.PolicyError, match="target_comment_is_public"):
        tools.TOOLS["merge_tickets"].check({"ticket_id": 1, "ids": [2], "target_comment_is_public": True})


# --- fix wave C3/C4: update_ticket and create_ticket forbid the reach side doors


@pytest.mark.parametrize(
    "key,value",
    [
        ("custom_status_id", 321),
        ("additional_collaborators", ["a@example.com"]),
        ("email_ccs", [{"user_email": "a@example.com", "action": "put"}]),
        ("followers", [{"user_email": "a@example.com", "action": "put"}]),
        ("collaborator_ids", [123]),
    ],
)
def test_update_ticket_forbids_the_reach_side_doors(key, value):
    with pytest.raises(exc.PolicyError, match=key):
        tools.TOOLS["update_ticket"].check({"ticket_id": 1, key: value})


@pytest.mark.parametrize(
    "key,value",
    [
        ("custom_status_id", 321),
        ("additional_collaborators", ["a@example.com"]),
        ("email_ccs", [{"user_email": "a@example.com", "action": "put"}]),
        ("followers", [{"user_email": "a@example.com", "action": "put"}]),
        ("collaborator_ids", [123]),
    ],
)
def test_create_ticket_forbids_the_reach_side_doors(key, value):
    with pytest.raises(exc.PolicyError, match=key):
        tools.TOOLS["create_ticket"].check({"subject": "help", key: value})


# --- Step 5: the failing integration tests for the seam -----------------------

from csa_zendesk import policy  # noqa: E402


def test_the_seam_refuses_a_subject_outside_the_write_allowlist(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    with pytest.raises(exc.PolicyError, match="44999"):
        policy.assert_subject_permitted("update_ticket", {"ticket_id": 44999})


def test_the_write_check_is_on_the_target_not_on_what_search_returned(monkeypatch):
    # READ=* is the normal posture: triage must see the whole queue. That must
    # not leak into write scope.
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_READ", "*")
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    policy.assert_subject_permitted("get_ticket", {"ticket_id": 99999})
    with pytest.raises(exc.PolicyError):
        policy.assert_subject_permitted("update_ticket", {"ticket_id": 99999})


# --- carried requirement 1: ids arrive as int and must be converted at the seam


def test_assert_subject_permitted_converts_an_int_ticket_id(monkeypatch):
    # Directly verifies the failure mode named in review: `_scope.permits`
    # compares against a `frozenset[str]`, so an unconverted `int` id is never a
    # member of it and every allowlisted write is refused - fail-closed, but for
    # the wrong reason. `44821` here is a genuine Python `int`, not a string.
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    policy.assert_subject_permitted("update_ticket", {"ticket_id": 44821})


# --- coverage: assert_subject_permitted's remaining branches -------------------


def test_assert_subject_permitted_is_a_no_op_for_a_tool_outside_the_table():
    policy.assert_subject_permitted("not_a_real_tool", {"ticket_id": 1})


def test_assert_subject_permitted_is_a_no_op_when_the_tool_names_no_allowlist():
    # search_tickets has no subject_var: nothing to scope a search's target to.
    policy.assert_subject_permitted("search_tickets", {})


def test_assert_subject_permitted_refuses_a_scoped_tool_with_no_subject_id():
    # update_trigger is scoped by CSA_ZD_ALLOWLIST_ADMIN but this call carries no
    # `ticket_id` - a programming error, not a configuration problem.
    with pytest.raises(exc.PolicyError, match="ticket_id"):
        policy.assert_subject_permitted("update_trigger", {"trigger_id": 1})
