# Block 0c — Tool-Surface Validation Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build eleven real tools over eight operations, so the tool-surface design is tested against the awkward cases before 335 operations are generated from it.

**Architecture:** A tool is `(operation × constrained arguments)` — ADR-016. The constraint is enforced at the same seam as the capability, so a library embedder gets the same refusal an MCP client does. Three operator controls, not two: toolset selects surface, capability grants authority, allowlist selects subject. Reach carries its own switch, off by default, separate from the capability profile.

**Tech Stack:** Python ≥3.10 · `pytest` · `ruff` · `mypy --strict`. **No MCP SDK, no network, no credentials** — everything runs against the existing `FakeBackend`.

**Spec:** [`DECISIONS-ADR/ADR-016.md`](../../../DECISIONS-ADR/ADR-016.md) (what a tool is), [`ADR-003`](../../../DECISIONS-ADR/ADR-003.md) (reversibility ordering, public replies are their own tool), [`ADR-006`](../../../DECISIONS-ADR/ADR-006.md) (toolsets vs capabilities), [`ADR-010`](../../../DECISIONS-ADR/ADR-010.md) (capabilities are derived), and [the whole-project design](../specs/2026-09-17-csa-zendesk-whole-project-design.md) §1 (the allowlist) and §4 (bucket purity, as corrected).

## This block is an experiment. It is allowed to fail.

Every other plan in this repo builds something. This one **tests a design**, and a task that
falsifies a design claim has succeeded. Task 6 writes up what held and what broke, and a finding of
"bucket purity does not survive contact" is a valid, valuable outcome — not a reason to force the
tools into shape.

The rule the block exists to test has already broken once, before any code: `PUT /tickets/{id}` is
five impact levels in one operation (ADR-016). That is why the table comes first and the machinery
second.

## Global Constraints

Block 0's constraints all still apply. The ones this block can break:

- **The constraint is enforced at the seam, not in the tool.** A tool that merely *documents* that it
  will not send a public comment is not a control. `PolicyBackend` refuses the call.
- **Reach is off by default and is not a capability profile member.** `CSA_ZD_ALLOW_REACH` must be
  explicitly true. Granting `ticket.reply` is necessary and **not sufficient**.
- **Fail closed.** An unlisted method is refused. A tool with no declared constraint is refused.
- **`ALLOWLIST` unusable means nothing permitted**, never "ignore the setting".
- **Never interpolate a credential** into a message, a log line, or a `__repr__`.
- **100% coverage, enforced** (`--cov-fail-under=100`). `mypy --strict` over `src`. `ruff` line length 120.
- **Nothing may write to stdout.**
- **Keyword-only arguments** on every `Backend` method.
- **Branch and PR**; `scripts/check_public_safe.py` passes before every push.

## File Structure

| File | Responsibility |
|---|---|
| `analysis/tool-boundaries.csv` | The table. One row per tool: operation, constraint, four axis values, capability. |
| `analysis/TOOL-BOUNDARIES.md` | What the table shows, and what it broke. Derived counts only. |
| `scripts/check_boundaries.py` | Fails if any tool's row is not bucket-pure, or if a tool has no constraint. |
| `src/csa_zendesk/policy.py` | Rebuilt capability model + the reach switch. Modified, not replaced. |
| `src/csa_zendesk/_scope.py` | The allowlist: parsing, the three outcomes, subject resolution. |
| `src/csa_zendesk/tools.py` | The eleven tool definitions and their argument constraints. |
| `tests/test_boundaries.py`, `tests/test_scope.py`, `tests/test_tools.py`, `tests/test_reach.py` | One module per concern. |

---

### Task 1: The tool-boundary table — by hand, no machinery

**Files:**
- Create: `analysis/tool-boundaries.csv`, `analysis/TOOL-BOUNDARIES.md`, `scripts/check_boundaries.py`
- Test: `tests/test_boundaries.py`

**Interfaces:**
- Consumes: `analysis/operation-classification.csv`.
- Produces: the CSV contract — columns `tool,method,path,constraint,effect,reversibility,reach,capability`.

**This is the task that can still kill the design.** It is deliberately first and deliberately cheap.

- [ ] **Step 1: Write the table**

Eleven tools over eight operations. `PUT /tickets/{id}` backs five of them.

```csv
tool,method,path,constraint,effect,reversibility,reach,capability
get_ticket,GET,/api/v2/tickets/{ticket_id},none,read,n/a,internal,ticket.read
search_tickets,GET,/api/v2/search,none,read,n/a,internal,ticket.read
create_ticket,POST,/api/v2/tickets,comment.public must be false or absent,write,reversible,internal,ticket.write
update_ticket,PUT,/api/v2/tickets/{ticket_id},body must not contain comment or status,write,reversible,internal,ticket.write
assign_ticket,PUT,/api/v2/tickets/{ticket_id},body may contain only assignee_id or group_id,write,reversible,internal,ticket.write
add_internal_note,PUT,/api/v2/tickets/{ticket_id},comment only; public forced false,note,reversible,internal,ticket.note
reply_publicly,PUT,/api/v2/tickets/{ticket_id},comment only; public forced true,reply,irreversible,contacts-a-person,ticket.reply
solve_ticket,PUT,/api/v2/tickets/{ticket_id},status only; solved,solve,reversible-for-a-period,internal,ticket.solve
close_ticket,PUT,/api/v2/tickets/{ticket_id},status only; closed,close,irreversible,internal,ticket.close
merge_tickets,POST,/api/v2/tickets/{ticket_id}/merge,none,close,irreversible,internal,ticket.close
update_trigger,PUT,/api/v2/triggers/{trigger_id},none,write,reversible,internal,admin.write
```

Two rows are the findings, not the design:

- **`assign_ticket` shares every axis value with `update_ticket`.** It is a *usability* split inside
  one bucket, not an impact split. Bucket purity permits it and does not require it. Record which.
- **`merge_tickets` is `ticket.close`, not `ticket.write`** — TODO **C6**, placed here by the axes
  rather than by someone remembering.

- [ ] **Step 2: Write the failing test for the bucket-purity checker**

```python
# tests/test_boundaries.py
import csv, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def rows():
    with (ROOT / "analysis/tool-boundaries.csv").open() as fh:
        return list(csv.DictReader(fh))


def test_every_tool_has_exactly_one_capability():
    for r in rows():
        assert r["capability"] and " " not in r["capability"], r["tool"]


def test_tools_sharing_an_operation_differ_on_an_axis_or_a_constraint():
    # The point of ADR-016: one operation backs several tools, and what separates
    # them is the constraint. Two tools on one operation with identical axes AND
    # no distinguishing constraint would be a duplicate, not a split.
    by_op: dict[tuple[str, str], list[dict]] = {}
    for r in rows():
        by_op.setdefault((r["method"], r["path"]), []).append(r)
    for op, tools in by_op.items():
        if len(tools) == 1:
            continue
        seen = set()
        for t in tools:
            key = (t["effect"], t["reversibility"], t["reach"], t["constraint"])
            assert key not in seen, f"{op}: {t['tool']} is indistinguishable from a sibling"
            seen.add(key)


def test_a_tool_on_a_shared_operation_must_carry_a_constraint():
    # If an operation backs more than one tool, "none" is not a legal constraint -
    # it would mean the tool can do everything its siblings were split apart for.
    by_op: dict[tuple[str, str], list[dict]] = {}
    for r in rows():
        by_op.setdefault((r["method"], r["path"]), []).append(r)
    for op, tools in by_op.items():
        if len(tools) > 1:
            for t in tools:
                assert t["constraint"] != "none", f"{t['tool']} shares {op} but constrains nothing"


def test_the_checker_script_passes():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/check_boundaries.py")], capture_output=True)
    assert r.returncode == 0, r.stdout.decode() + r.stderr.decode()
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest tests/test_boundaries.py -v`
Expected: FAIL — `FileNotFoundError: analysis/tool-boundaries.csv` (before Step 1's file is added) or the checker is missing.

- [ ] **Step 4: Write the checker**

```python
#!/usr/bin/env python3
"""Fail if the tool-boundary table is not bucket-pure.

ADR-016: a tool is (operation × constrained arguments), and the constraint is
what makes it bucket-pure. This is the enforcement of that sentence - the table
is hand-written, and a hand-written table drifts.
"""
from __future__ import annotations

import collections
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
AXES = ("effect", "reversibility", "reach", "capability")


def main() -> int:
    with (ROOT / "analysis/tool-boundaries.csv").open() as fh:
        rows = list(csv.DictReader(fh))

    problems: list[str] = []
    by_op: dict[tuple[str, str], list[dict[str, str]]] = collections.defaultdict(list)
    for r in rows:
        by_op[(r["method"], r["path"])].append(r)
        if not r["capability"] or " " in r["capability"]:
            problems.append(f"{r['tool']}: needs exactly one capability, got {r['capability']!r}")

    for op, tools in by_op.items():
        if len(tools) == 1:
            continue
        for t in tools:
            if t["constraint"] == "none":
                problems.append(f"{t['tool']}: shares {op[0]} {op[1]} with a sibling but constrains nothing")
        keys = [tuple(t[a] for a in AXES) + (t["constraint"],) for t in tools]
        if len(set(keys)) != len(keys):
            problems.append(f"{op[0]} {op[1]}: two tools are indistinguishable")

    for p in problems:
        print(f"  {p}", file=sys.stderr)
    print(f"{'REFUSED' if problems else 'OK'} - {len(rows)} tools over {len(by_op)} operations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_boundaries.py -v && python3 scripts/check_boundaries.py`
Expected: 4 passed; `OK - 11 tools over 8 operations`

- [ ] **Step 6: Write `analysis/TOOL-BOUNDARIES.md`**

Record, with counts derived by running the checker rather than typed: eleven tools over eight
operations; five of them over one `PUT`; the two findings from Step 1; and whether anything in the
table required bending the rule. If something did, say so plainly — that is the block's output.

- [ ] **Step 7: Commit**

```bash
git add analysis/tool-boundaries.csv analysis/TOOL-BOUNDARIES.md scripts/check_boundaries.py tests/test_boundaries.py
git commit -m "analysis: eleven tools over eight operations, and the rule that checks them"
```

---

### Task 2: Rebuild the capability model from the table

**Files:**
- Modify: `src/csa_zendesk/policy.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: Task 1's `analysis/tool-boundaries.csv`.
- Produces: `ALL_CAPABILITIES` containing only capabilities some tool uses; `PROFILES` without reach members.

This removes three of the four contradictions on `main` in one change.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_policy.py
import csv, pathlib
from csa_zendesk import policy

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _table_capabilities() -> set[str]:
    with (ROOT / "analysis/tool-boundaries.csv").open() as fh:
        return {r["capability"] for r in csv.DictReader(fh)}


def test_no_capability_exists_that_no_tool_uses():
    # The Never bucket is a build-time control: an operation that is never
    # generated cannot be enabled by misconfiguration. A capability for an
    # operation we will never build is a promise the code does not keep.
    unused = set(policy.ALL_CAPABILITIES) - _table_capabilities() - {policy.BULK}
    assert unused == set(), f"capabilities with no tool: {sorted(unused)}"


def test_the_refused_operations_have_no_capability_at_all():
    # analysis/scope-triage-exceptions.csv refuses purge, the two merges and
    # mark_as_spam outright. They must not be grantable.
    for gone in ("ticket.purge", "people.purge", "people.merge", "people.suspend"):
        assert gone not in policy.ALL_CAPABILITIES


def test_no_profile_grants_a_reach_capability():
    # DEC-015: reach carries an operator switch SEPARATE from the capability
    # profile. A profile that grants ticket.reply makes the switch decorative.
    for name, caps in policy.PROFILES.items():
        assert policy.TICKET_REPLY not in caps, f"profile {name!r} grants reach"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_policy.py -k "unused or refused or reach" -v`
Expected: 3 FAILED — `ticket.purge` etc. still present, and `agent`/`full` still grant `TICKET_REPLY`.

- [ ] **Step 3: Remove the refused capabilities and take reach out of every profile**

In `policy.py`: delete `TICKET_PURGE`, `PEOPLE_PURGE`, `PEOPLE_MERGE`, `PEOPLE_SUSPEND` and their
`ALL_CAPABILITIES` entries. Remove `TICKET_REPLY` from the `agent` profile. Replace the `full`
subtraction set, which no longer needs to name capabilities that do not exist:

```python
    # `full` is every capability that exists, minus the ones no word should grant.
    # Ordered by REACH first and destructiveness second (DEC-015): ticket.reply is
    # excluded although it destroys nothing, because its effect leaves the building.
    # Reach additionally requires CSA_ZD_ALLOW_REACH - a profile cannot grant it.
    "full": frozenset(ALL_CAPABILITIES) - {TICKET_REPLY, TICKET_CLOSE, RAW_READ, RAW_WRITE},
```

Leave a comment where the deleted capabilities were:

```python
# ticket.purge, people.purge, people.merge and people.suspend used to live here.
# analysis/scope-triage-exceptions.csv refuses those operations outright, so no
# tool backs them and a capability for them would be a promise the code does not
# keep. Re-adding one means re-admitting the operation first, deliberately.
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_policy.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/policy.py tests/test_policy.py
git commit -m "fix(policy): drop capabilities no tool uses, and take reach out of every profile"
```

---

### Task 3: The allowlist — the third control

**Files:**
- Create: `src/csa_zendesk/_scope.py`
- Test: `tests/test_scope.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Listing` (`all_subjects: bool`, `ids: frozenset[str]`), `read_listing(var: str) -> Listing`, `permits(listing: Listing, subject: str) -> bool`, exception `AllowlistError`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scope.py
import pytest
from csa_zendesk import _scope


def test_star_means_everything_and_is_distinct_from_empty(monkeypatch):
    monkeypatch.setenv("X", "*")
    assert _scope.read_listing("X").all_subjects is True


def test_an_unset_variable_permits_nothing(monkeypatch):
    # Fail closed. Absent is not "no restriction".
    monkeypatch.delenv("X", raising=False)
    listing = _scope.read_listing("X")
    assert listing.all_subjects is False and listing.ids == frozenset()
    assert _scope.permits(listing, "44821") is False


def test_entries_parse_with_reasons_and_comments(monkeypatch):
    monkeypatch.setenv("X", "44821  # the triage test ticket\n44822, 44823\n\n# a whole-line comment\n")
    listing = _scope.read_listing("X")
    assert listing.ids == frozenset({"44821", "44822", "44823"})


def test_an_unusable_value_is_an_error_not_a_silent_pass(monkeypatch):
    # ADR-equivalent of csa-google-workspace's third outcome: unusable always
    # means NOTHING permitted, never "ignore the setting".
    monkeypatch.setenv("X", "44821, not-an-id")
    with pytest.raises(_scope.AllowlistError, match="not-an-id"):
        _scope.read_listing("X")


def test_permits_is_exact_not_prefix(monkeypatch):
    monkeypatch.setenv("X", "4482")
    listing = _scope.read_listing("X")
    assert _scope.permits(listing, "4482") is True
    assert _scope.permits(listing, "44821") is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_scope.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'csa_zendesk._scope'`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/_scope.py
"""Allowlists: which objects may be acted on at all.

The third operator control (whole-project design §1). Toolsets select surface,
capabilities grant authority, allowlists select SUBJECT - and all three are
enforced at the same seam so a library embedder gets the same refusal an MCP
client does.

**This is a blast-radius control, not a security boundary.** The server acts as
the operating user, so nothing is reachable here that is not already reachable
in Zendesk. It protects against the agent doing the wrong thing; it does not
protect against a user exceeding their authority, and for an administrator it
constrains almost nothing. Anyone who grants this tool a wider credential
believing the allowlist will hold it has misread it.

Shape adopted from csa-google-workspace's allowlist.py: configured in the
environment so the policy lives where the operator declares the server and can
see it; three outcomes, where unusable means NOTHING permitted; and `*` distinct
from empty, because one is a deliberate decision and the other is a typo.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import exceptions as exc

_ID = re.compile(r"^[0-9]+$")


class AllowlistError(exc.ZendeskError):
    """The allowlist is configured but unusable. Never degrades to 'no restrictions'."""


@dataclass(frozen=True, slots=True)
class Listing:
    all_subjects: bool
    ids: frozenset[str] = frozenset()


def read_listing(var: str) -> Listing:
    raw = os.environ.get(var, "")
    if raw.strip() == "*":
        return Listing(all_subjects=True)
    ids: set[str] = set()
    for line in raw.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", line).strip()
        for piece in (p.strip() for p in line.split(",")):
            if not piece:
                continue
            if not _ID.match(piece):
                raise AllowlistError(
                    f"{var}: {piece!r} is not a numeric id. Entries are Zendesk ids, one per line "
                    f"or comma-separated, with `#` starting a comment. Nothing is permitted while "
                    f"this value is unusable."
                )
            ids.add(piece)
    return Listing(all_subjects=False, ids=frozenset(ids))


def permits(listing: Listing, subject: str) -> bool:
    return listing.all_subjects or subject in listing.ids
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_scope.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/_scope.py tests/test_scope.py
git commit -m "feat(scope): allowlists as the third control, failing closed on anything unusable"
```

---

### Task 4: The reach switch

**Files:**
- Modify: `src/csa_zendesk/policy.py`
- Test: `tests/test_reach.py`

**Interfaces:**
- Consumes: Task 2's capability set.
- Produces: `REACH_CAPABILITIES: frozenset[str]`, `reach_permitted() -> bool`, and a refusal in `PolicyBackend` when a reaching call is attempted without the switch.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reach.py
import pytest
from csa_zendesk import exceptions as exc
from csa_zendesk import policy


def test_the_capability_alone_is_not_enough(monkeypatch):
    # DEC-015: reach is a first-class control with an operator switch SEPARATE
    # from the capability profile, off by default. Holding ticket.reply and
    # nothing else must still refuse.
    monkeypatch.delenv("CSA_ZD_ALLOW_REACH", raising=False)
    assert policy.reach_permitted() is False


def test_the_switch_must_be_explicit(monkeypatch):
    for value in ("", "0", "no", "false", "maybe"):
        monkeypatch.setenv("CSA_ZD_ALLOW_REACH", value)
        assert policy.reach_permitted() is False, value
    monkeypatch.setenv("CSA_ZD_ALLOW_REACH", "true")
    assert policy.reach_permitted() is True


def test_the_refusal_names_the_switch_not_the_capability(monkeypatch):
    # "Every refusal names its own remedy." Telling an operator to grant
    # ticket.reply when they already hold it sends them to the wrong knob.
    monkeypatch.delenv("CSA_ZD_ALLOW_REACH", raising=False)
    with pytest.raises(exc.PolicyError, match="CSA_ZD_ALLOW_REACH"):
        policy.assert_reach_permitted("reply_publicly")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_reach.py -v`
Expected: FAIL — `AttributeError: module has no attribute 'reach_permitted'`

- [ ] **Step 3: Implement**

```python
# in src/csa_zendesk/policy.py
#: Capabilities whose effect leaves the building and touches a person (DEC-015).
#: These need the operator switch IN ADDITION to the capability - holding
#: `ticket.reply` is necessary and not sufficient.
REACH_CAPABILITIES: frozenset[str] = frozenset({TICKET_REPLY})


def reach_permitted() -> bool:
    """Whether outward-facing calls are allowed at all. Off unless explicitly on."""
    return os.environ.get("CSA_ZD_ALLOW_REACH", "").strip().lower() == "true"


def assert_reach_permitted(tool: str) -> None:
    if not reach_permitted():
        raise exc.PolicyError(
            f"`{tool}` sends something to a person outside this organisation, and outward-facing "
            f"calls are off. Set CSA_ZD_ALLOW_REACH=true to enable them. This is deliberately "
            f"separate from the capability profile: a public reply cannot be unsent, so granting "
            f"the capability is necessary and not sufficient."
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_reach.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/policy.py tests/test_reach.py
git commit -m "feat(policy): reach is a switch, not a capability"
```

---

### Task 5: The eleven tools, with constraints enforced at the seam

**Files:**
- Create: `src/csa_zendesk/tools.py`
- Modify: `src/csa_zendesk/policy.py` (wire scope + reach into the dispatch)
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: `TOOLS: dict[str, ToolSpec]` where `ToolSpec` carries `capability`, `reach: bool`, `subject_var: str | None`, and `check(kwargs) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tools.py
import pytest
from csa_zendesk import exceptions as exc
from csa_zendesk import tools


def test_every_tool_in_the_table_exists_in_code():
    import csv, pathlib
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'csa_zendesk.tools'`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/tools.py
"""The tool surface. ADR-016: a tool is (operation × constrained arguments).

One operation backs several tools, and **the constraint on the request body is
what makes a tool bucket-pure**. `PUT /tickets/{id}` is five impact levels -
field edit, internal note, public reply, solve, close - so it backs five tools,
each refusing the body keys that would change its bucket.

The constraints are enforced, not documented. A tool that merely says it will
not send a public comment is not a control.
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
    #: act on no particular subject (a search).
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
                f"this tool accepts only {sorted(allowed)}; got {extra}. Use the tool built for "
                f"those fields."
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
        # ticket defaults to PUBLIC (CLAUDE.md invariant 13). Omitting it here
        # would make the reach of this call depend on the ticket's history.
        comment["public"] = value
    return check


def _status(value: str) -> Callable[[dict[str, Any]], None]:
    def check(kwargs: dict[str, Any]) -> None:
        _only("status")(kwargs)
        if kwargs.get("status") != value:
            raise exc.PolicyError(f"this tool sets status={value!r} only")
    return check


TOOLS: dict[str, ToolSpec] = {
    "get_ticket": ToolSpec("ticket.read", subject_var="CSA_ZD_ALLOWLIST_READ"),
    "search_tickets": ToolSpec("ticket.read"),
    "create_ticket": ToolSpec("ticket.write", check=_forbid("status")),
    "update_ticket": ToolSpec(
        "ticket.write", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_forbid("comment", "status")
    ),
    "assign_ticket": ToolSpec(
        "ticket.write", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_only("assignee_id", "group_id")
    ),
    "add_internal_note": ToolSpec(
        "ticket.note", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_force_public(False)
    ),
    "reply_publicly": ToolSpec(
        "ticket.reply", reach=True, subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_force_public(True)
    ),
    "solve_ticket": ToolSpec(
        "ticket.solve", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_status("solved")
    ),
    "close_ticket": ToolSpec(
        "ticket.close", subject_var="CSA_ZD_ALLOWLIST_WRITE", check=_status("closed")
    ),
    "merge_tickets": ToolSpec("ticket.close", subject_var="CSA_ZD_ALLOWLIST_WRITE"),
    "update_trigger": ToolSpec("admin.write", subject_var="CSA_ZD_ALLOWLIST_ADMIN"),
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_tools.py -v`
Expected: 9 passed

- [ ] **Step 5: Write the failing integration tests for the seam**

```python
# append to tests/test_tools.py
from csa_zendesk import policy


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
```

- [ ] **Step 6: Wire scope and reach into `PolicyBackend`'s dispatch**

In `policy.py`, add `assert_subject_permitted(tool, kwargs)` reading `TOOLS[tool].subject_var`
through `_scope`, and call both it and `assert_reach_permitted` from `_dispatch` before delegating —
after the capability check, so the most specific refusal wins.

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -q --cov=csa_zendesk --cov-fail-under=100`
Expected: all pass, 100%

- [ ] **Step 8: Commit**

```bash
git add src/csa_zendesk/tools.py src/csa_zendesk/policy.py tests/test_tools.py
git commit -m "feat(tools): eleven tools over eight operations, constraints enforced at the seam"
```

---

### Task 6: The findings — what held and what broke

**Files:**
- Create: `analysis/SLICE-FINDINGS.md`
- Modify: `TODO.md`, `docs/superpowers/specs/2026-09-17-csa-zendesk-whole-project-design.md`

This is the block's actual deliverable. The tools are the instrument; this is the reading.

- [ ] **Step 1: Answer each claim, with the evidence**

Four claims the slice was built to test. For each: **held**, **broke**, or **held with a change**,
and the test or table row that shows it.

1. **Bucket purity** — does every tool sit in one bucket? (Already broken once, before the block:
   ADR-016. Did the rebuilt form survive eleven tools?)
2. **Three controls** — do toolset, capability and allowlist compose without leaking? (`test_the_write_check_is_on_the_target…`)
3. **Reach as a separate switch** — is the capability genuinely insufficient? (`test_the_capability_alone_is_not_enough`)
4. **The `Never` bucket** — did `policy.py` compile with those capabilities deleted, or did something need them? (`test_no_capability_exists_that_no_tool_uses`)

- [ ] **Step 2: Record what the eleven tools cost**

Eight operations became eleven tools. Extrapolate honestly: if the ticket-write path multiplies and
the read path does not, what does 335 admitted operations imply for the tool count — and is that
still a surface a model can choose from? `ADR-006` notes the widest server surveyed ships 51 tools.
If the answer is "more than 51", say so; it is a real finding about the design, not a detail.

- [ ] **Step 3: File what the slice surfaced**

Anything the tools revealed goes into `TODO.md` as a numbered item. Expect at least: whether
`assign_ticket` sets a precedent for usability splits inside a bucket (Task 1 Step 1), and whether
`policy.py`'s callable `Gate` now has a user (**B22**).

- [ ] **Step 4: Update the design if a claim broke**

A correction appended in place, the way §4 already carries one — never a silent rewrite.

- [ ] **Step 5: Commit**

```bash
git add analysis/SLICE-FINDINGS.md TODO.md docs/
git commit -m "analysis: what the tool slice proved, and what it did not"
```

---

## Self-Review

**Spec coverage.** ADR-016's "a tool is `(operation × constrained arguments)`" → Tasks 1 and 5;
"tools are atomic, no composites" → the table has no composite row and `test_every_tool_in_the_table_exists_in_code`
pins the two together; "workflow lives in plugins" → out of scope by construction, tracked as **B21**.
Design §1's three controls → Tasks 3 and 5; the read/write asymmetry → `test_the_write_check_is_on_the_target…`.
DEC-015's reach-as-a-switch → Task 4. ADR-010's derived capabilities → Task 2. TODO **C6** → the
`merge_tickets` row and its test.

**Two design elements deliberately not built.** The **provenance tag** (`csa-zendesk-created`, design
§1 rule 1) needs `create_ticket` to reach a real Zendesk to be meaningful, and this block has no
network; the allowlist is tested with operator-granted ids only, and provenance scope arrives with
Block 0b. The **`ADMIN` allowlist** is wired (`update_trigger` carries `subject_var`) but only one
admin tool exercises it, so the finding it exists to produce — whether per-object admin scope is
workable at scale — is out of reach until more admin tools exist. Both are recorded here rather than
discovered as gaps.

**Placeholder scan.** No TBD, no "add error handling", no "similar to Task N". Every code step
carries its code. Task 6 is prose by design — it is a findings report, and its steps specify exactly
which claims to answer and which tests answer them.

**Type consistency.** `ToolSpec(capability, reach, subject_var, check)` is constructed once in Task 5
and read in Tasks 5 and 6 with those names. `Listing(all_subjects, ids)` is produced by
`read_listing` and consumed by `permits` in Task 3. The CSV contract
`tool,method,path,constraint,effect,reversibility,reach,capability` is written in Task 1 and read by
Tasks 1, 2 and 5 — the column names match in all three.
