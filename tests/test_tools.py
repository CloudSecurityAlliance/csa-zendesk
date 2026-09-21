import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk import tools


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
    # The whole of ADR-016 in one assertion: the constraint is the control. A
    # tool that merely documents "I will not comment" is not a control.
    with pytest.raises(exc.PolicyError, match="comment"):
        tools.TOOLS["update_ticket"].check({"ticket_id": 1, "comment": {"body": "hi"}})


def test_update_ticket_refuses_a_status():
    with pytest.raises(exc.PolicyError, match="status"):
        tools.TOOLS["update_ticket"].check({"ticket_id": 1, "status": "solved"})


def test_update_ticket_permits_a_field_edit():
    tools.TOOLS["update_ticket"].check({"ticket_id": 1, "priority": "high"})


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
