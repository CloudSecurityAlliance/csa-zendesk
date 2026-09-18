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


def test_add_internal_note_forces_public_false():
    kwargs = {"ticket_id": 1, "comment": {"body": "note", "public": True}}
    tools.TOOLS["add_internal_note"].check(kwargs)
    assert kwargs["comment"]["public"] is False  # forced, not refused


def test_reply_publicly_forces_public_true_and_is_flagged_for_reach():
    kwargs = {"ticket_id": 1, "comment": {"body": "hello"}}
    tools.TOOLS["reply_publicly"].check(kwargs)
    assert kwargs["comment"]["public"] is True
    assert tools.TOOLS["reply_publicly"].reach is True


def test_only_reply_publicly_reaches_a_person():
    reaching = {n for n, t in tools.TOOLS.items() if t.reach}
    assert reaching == {"reply_publicly"}


def test_merge_is_gated_at_close_not_write():
    # TODO C6: merging closes the source ticket. Placed by the axes, not by
    # someone remembering.
    assert tools.TOOLS["merge_tickets"].capability == "ticket.close"


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


def test_add_internal_note_requires_a_comment_object():
    with pytest.raises(exc.PolicyError, match="comment"):
        tools.TOOLS["add_internal_note"].check({"ticket_id": 1})


def test_reply_publicly_requires_a_comment_object():
    with pytest.raises(exc.PolicyError, match="comment"):
        tools.TOOLS["reply_publicly"].check({"ticket_id": 1})


def test_solve_ticket_sets_status_solved_only():
    tools.TOOLS["solve_ticket"].check({"ticket_id": 1, "status": "solved"})
    with pytest.raises(exc.PolicyError, match="solved"):
        tools.TOOLS["solve_ticket"].check({"ticket_id": 1, "status": "closed"})


def test_close_ticket_sets_status_closed_only():
    tools.TOOLS["close_ticket"].check({"ticket_id": 1, "status": "closed"})
    with pytest.raises(exc.PolicyError, match="closed"):
        tools.TOOLS["close_ticket"].check({"ticket_id": 1, "status": "solved"})


def test_get_ticket_and_search_tickets_and_merge_and_update_trigger_have_no_op_checks():
    # These four use ToolSpec's default no-op check - there is no constraint to
    # enforce beyond capability, scope and (where relevant) reach.
    tools.TOOLS["get_ticket"].check({"ticket_id": 1})
    tools.TOOLS["search_tickets"].check({"query": "status:open"})
    tools.TOOLS["merge_tickets"].check({"ticket_id": 1, "target_ticket_id": 2})
    tools.TOOLS["update_trigger"].check({"trigger_id": 1})


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
