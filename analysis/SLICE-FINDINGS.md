# Slice findings — what the tool surface proved, and what it did not

**Date:** 2026-09-18
**Status:** Block 0c, Task 6 — the block's deliverable. Read this after
[`ADR-016`](../DECISIONS-ADR/ADR-016.md) and [`analysis/TOOL-BOUNDARIES.md`](TOOL-BOUNDARIES.md).
**Specified by:** [`docs/superpowers/plans/2026-09-17-block-0c-tool-slice.md`](../docs/superpowers/plans/2026-09-17-block-0c-tool-slice.md),
whose "This block is an experiment. It is allowed to fail." section is the standard this document
is held to.

Block 0c built eleven tools over six operations to test four claims from the whole-project design
before 335 admitted operations get generated from it. This is the reading, not the instrument.
Every count below was run, not typed.

```
$ python3 scripts/check_boundaries.py
OK - 11 tools over 6 operations

$ /tmp/zdvenv-0c/bin/python -m pytest -q
188 passed

$ /tmp/zdvenv-0c/bin/python -m pytest -q --cov=csa_zendesk --cov-report=term-missing
TOTAL  475 stmts, 0 miss, 100% cover

$ python3 scripts/check_public_safe.py
OK - 95 tracked files, nothing tenant-specific found
coverage: structural + tenant terms
```

*(Corrected 2026-09-18, final review, Minor: this repository now has **96** tracked files — this
document's own addition moved the count by one. Also, `check_boundaries.py`'s `OK - ...` line above
is written to **stderr**, not stdout (`print(..., file=sys.stderr)`); shown here as it would appear
in an interactive shell, not as a literal capture of `subprocess.run(...).stdout`.)*

`analysis/scope-triage.csv`, via `scripts/triage.py`, gives the admitted/deferred/refused/blocked
split this slice sits inside:

```
bucket     families  operations   share
now              33         335    40.8%
later            56         387    47.1%
never             9          48     5.8%
blocked          10          52     6.3%
total           108         822
```

Eleven tools were built against 6 of those 335 admitted operations. Nothing below should be read
past that ratio.

---

## The four claims

### 1. Bucket purity — held, with a change, and a known gap in how it's checked

**Verdict: held with a change.** ADR-016 redefined a tool as `(operation × constrained arguments)`
after the undifferentiated view of `PUT /tickets/{id}` broke bucket purity before any code existed.
The question for this block was whether that redefinition survives eleven real tools. It does:
`scripts/check_boundaries.py` reports `OK` and all four `tests/test_boundaries.py` tests pass
(`test_every_tool_has_exactly_one_capability`, `test_tools_sharing_an_operation_differ_on_an_axis_or_a_constraint`,
`test_a_tool_on_a_shared_operation_must_carry_a_constraint`, `test_the_checker_script_passes`).

The change: the plan's own draft constraint for `create_ticket` (`comment.public must be false or
absent`) was insufficient — `POST /tickets` also accepts `status`, so an unconstrained
`create_ticket` could create-and-solve in one call, spanning the write and solve/close buckets the
same way the undifferentiated `PUT` did. This was caught and fixed *before* Task 1 wrote any code
(progress.md Ruling 2), and the table now carries the corrected two-part constraint: "no public
comment; body must not contain status" (`analysis/tool-boundaries.csv`, `create_ticket` row;
`src/csa_zendesk/tools.py::_create_ticket_check`).

The gap that remains, named in `analysis/TOOL-BOUNDARIES.md`'s own "Did anything require bending the
rule?" section: `check_boundaries.py` only catches impurity by comparing tools that **share** an
operation. Five of this slice's six operations back exactly one tool each, so they get no mechanical
check at all — `create_ticket`'s fix above was caught by a human re-reading the operation's field
surface, not by the checker. Filed as **B24**, and it is the single most consequential finding this
slice produced for the 822-operation derivation: a solo-tool operation is precisely the one nobody
thought to split, and it is exactly the case the mechanical check cannot see.

> **Correction (2026-09-18, final review).** The verdict above — "held, with a change" — is wrong,
> and wrong in the direction that understates danger, which is the one direction a document whose
> job is to stop a reader concluding too much cannot afford. **The corrected verdict: bucket purity
> held only where the tool's constraint is an allowlist, and broke again wherever the constraint is
> a denylist.**
>
> The five tools built with `_only(...)` — an allowlist of the keys a call may carry —
> (`assign_ticket`, `add_internal_note`, `reply_publicly`, `solve_ticket`, `close_ticket`) are
> genuinely bucket-pure: nothing outside the named keys can reach the backend, by construction. The
> four built with `_forbid(...)` (a denylist of the keys a call may *not* carry) or no check at all —
> `update_ticket`, `create_ticket`, `merge_tickets`, `update_trigger` — are not, **and cannot be
> while they stay denylists: a denylist over a body the tool never fully enumerates is unclosable.**
> Naming the keys known to be dangerous today does not guarantee the next Zendesk API revision, or
> the next unread paragraph of the OAS, adds no others.
>
> This was not hypothetical. Before this correction's fix wave, `update_ticket` and `create_ticket`
> were both missing four more keys the OAS already documented: `custom_status_id` (a second route
> into `solve`/`close` alongside the literal `status` key — the OAS's own update example pairs
> `custom_status_id: 321` with `status: solved`) and the collaborator fields
> `additional_collaborators`, `email_ccs`, `followers`, `collaborator_ids` (each notifies someone
> when the ticket changes — reach, from tools that carried no `reach=True` flag). `merge_tickets`
> was worse: `POST .../merge` accepts `source_comment_is_public` and `target_comment_is_public` —
> the exact mechanism classified as reach for a public reply — and the tool was tabled `reach=internal`,
> `ToolSpec(reach=False)`, gated at `ticket.close` (a capability not in `REACH_CAPABILITIES`), with
> **no constraint at all**. All three tools are corrected in the same commit as this note: see
> `analysis/tool-boundaries.csv` and `src/csa_zendesk/tools.py`. `merge_tickets` now carries its own
> `ticket.merge` capability rather than sharing `close_ticket`'s `ticket.close` — deliberately, so
> that a tool which can reach a person and a tool which structurally cannot (`close_ticket` accepts
> no comment at all) are never forced to agree on one reach flag by sharing one capability string.
>
> **This reframes B24.** The predicate filed there — "the checker is blind to an operation that backs
> exactly one tool" — is wrong. `update_ticket` shares `PUT /tickets/{id}` with five siblings and
> `check_boundaries.py` inspects it on every run, and it was still wrong: the checker asserts a
> constraint *exists* and *distinguishes siblings*, never that a denylist constraint is *complete*.
> Operation cardinality was never the load-bearing variable; allowlist-vs-denylist is. See B24's
> corrected entry in `TODO.md` for the full re-framing.

### 2. Three controls compose without leaking — held

**Verdict: held.** `test_the_write_check_is_on_the_target_not_on_what_search_returned`
(`tests/test_tools.py`) sets `CSA_ZD_ALLOWLIST_READ=*` (the normal triage posture — see the whole
queue) and `CSA_ZD_ALLOWLIST_WRITE=44821`, then shows `get_ticket` on ticket `99999` is permitted
while `update_ticket` on the same ticket is refused: the read allowlist's breadth does not leak into
the write check. `test_get_ticket_through_the_real_dispatch_is_refused_outside_the_read_allowlist`
and `test_get_ticket_through_the_real_dispatch_permits_an_allowlisted_subject` (`tests/test_policy.py`)
confirm this is enforced by the real, materialised `_dispatch` path, not only by calling
`assert_subject_permitted` directly. `test_dispatch_fails_closed_when_the_read_allowlist_is_entirely_unset`
closes the one composition gap the Task 5 implementer flagged unprompted: every other test in the
suite runs with both allowlists defaulted to `*` by an autouse fixture, so fail-closed-on-unset was
exercised at module level only until this test opted out and drove it through a genuine
`PolicyBackend.get_ticket` call.

### 3. Reach as a separate switch — held

**Verdict: held.** `test_the_capability_alone_is_not_enough` (`tests/test_reach.py`) confirms the
switch defaults to off independent of any capability held. The stronger proof is
`test_reach_is_derived_from_the_calls_required_capabilities_not_hand_listed` and
`test_reach_derivation_lets_the_call_through_once_the_switch_is_on` (`tests/test_policy.py`): a
policy holding `ticket.reply` and nothing else is still refused, naming `CSA_ZD_ALLOW_REACH`, until
the switch is set — and the mechanism is `REACH_CAPABILITIES` intersected against whatever
capabilities the call's gate actually required, not a second hand-maintained list of tool names
(the Task 4 minor "REACH_CAPABILITIES is currently decorative" is resolved: it is now read from
`_dispatch`).

### 4. The `Never` bucket — held, but the brief's own pointer to it was wrong

**Verdict: held**, on the test that actually proves it. Task 6's own brief points at
`test_no_capability_exists_that_no_tool_uses` — that test does not exist in this codebase. It was
written into the plan, dispatched to Task 2, and struck by the controller **before any code ran**
(progress.md Ruling 1): it asserted `ALL_CAPABILITIES - table_capabilities - {BULK} == set()`, which
would have deleted `people.*`, `hc.*`, `reporting.*`, `raw.*` and `ticket.delete` — capabilities the
wider 335-operation admitted surface still needs, even though this slice's eleven tools don't touch
them. Obeying the brief's own test would have broken the claim it was meant to prove.

The invariant that actually answers the question is `test_the_refused_operations_have_no_capability_at_all`
(`tests/test_policy.py`), which passes: `ticket.purge`, `people.purge`, `people.merge` and
`people.suspend` are not in `ALL_CAPABILITIES` (`src/csa_zendesk/policy.py`, comment above
`TICKET_READ`). `policy.py` compiles, all 188 tests pass, and nothing needed those four capabilities
— the `Never` bucket's operations (mark-as-spam, the two merges, permanent purge) have no tool and no
capability, and removing the capability broke nothing because nothing was built to use it. Held —
and the fact that the wrong test was caught pre-code, by the pre-flight scan the plan requires,
is itself a working instance of the practice this repo asks for, not a footnote.

---

## What the eleven tools cost

Six operations became eleven tools; `PUT /tickets/{id}` alone backs six of them — a 6:1 split on
that one operation, driven entirely by the fact that Zendesk's ticket-update body spans five impact
levels. `create_ticket`, `merge_tickets`, `get_ticket`, `search_tickets` and `update_trigger` are all
1:1.

The whole-project design's §4 projected, under the assumption tools would be built by **grouping**
operations, "eight toolsets across four-to-six live buckets each puts the curated list around
**30–50 tools**." ADR-016 already replaced grouping with splitting for the case that mattered enough
to force the decision. This slice is the first place that trade is priced: one write-shaped
operation produced six tools, not a fraction of one.

Extrapolating honestly, not precisely — the full per-field classification needed to do this exactly
is exactly what **B18** and **B24** are still owed — 335 admitted operations do not all look like
`PUT /tickets/{id}`. Most of the admitted surface is read, simple CRUD, or single-purpose (search,
metrics, comments), and those stay near 1:1. But the admitted set also includes triggers,
automations, macros, views, ticket forms, workspaces and other config objects whose update operations
are exactly the "one PUT, several impact levels" shape that produced the 6:1 split here — and there
are dozens of such families in the "now" bucket, not one. If even a modest fraction of them split
the way the ticket write path did, the honest range is not "somewhat above 30–50" — it very plausibly
exceeds `ADR-006`'s own record of the widest server surveyed shipping **51 tools**, by a wide margin,
while the core ticket loop this project actually exists for is about ten.

> **Correction (2026-09-18, final review, I2/I3).** "Dozens of such families" overstates what this
> slice's own numbers support: the `now` bucket has **33 families total** (the scope-triage table
> above), of which roughly fourteen are config objects with a write — "several", not "dozens". The
> correction already appended to
> `docs/superpowers/specs/2026-09-17-csa-zendesk-whole-project-design.md` §4 (also 2026-09-18) says
> "several", correctly; this document is brought into line with it.
>
> More importantly, the extrapolation's *premise* is contradicted by this slice's own second
> datapoint, stated two paragraphs above the passage it contradicts: `update_trigger`, the one
> config-object write this slice actually built, came out **1:1**, constraint `none` — not a multi-
> split. The 6:1 split on `PUT /tickets/{id}` was driven by that operation carrying reach (a public
> reply) and a terminal status change in the same request body; a trigger `PUT` carries neither. The
> honest read of this slice's two datapoints is "the ticket write path splits hard; the one
> config-object write sampled does not" — not "config-object writes split the way the ticket write
> path did." Whether they still might, for reasons this slice's one sample didn't hit, remains open
> and is exactly what **B18**'s real per-operation classification is for; it is not something this
> slice's own evidence supports predicting.

**That is a finding about the design, not a detail.** A tool surface a model can usefully choose
from and a tool surface that is bucket-pure at Zendesk's actual granularity are now in tension, and
nothing in the design resolves it yet — toolsets (`ADR-006`) narrow what's *loaded* per session but
do not shrink the total that must exist to stay bucket-pure. Filed as **B28**, and a correction is
appended to the design doc's §4 below rather than silently rewriting its "30–50 tools" estimate.

---

## The most important thing this document has to say plainly

The enforcement seam — capability → constraint → scope → reach, in that order — is real and it is
proven by mutation, not by inspection: `test_a_tools_check_is_enforced_by_dispatch_itself_not_only_unit_tested`
shows that neutralising `spec.check(kwargs)` in `_dispatch` makes the test fail, and
`test_reach_is_derived_from_the_calls_required_capabilities_not_hand_listed` shows the same for the
reach wiring (progress.md, Task 5 re-review). That work is sound and it is exactly what Block 0c was
for.

But look at what those two mutation-tested proofs actually exercise. `policy._GATES` — the table
that says which capability a call needs before it can reach a real `Backend` method at all — contains
**exactly one entry**:

```python
_GATES: dict[str, Gate] = {
    "get_ticket": TICKET_READ,
}
```

`get_ticket`'s own `ToolSpec.check` is `tools.ToolSpec`'s default, a no-op lambda — `get_ticket` has
no constraint to enforce beyond capability and scope, by design (`tests/test_tools.py::test_get_ticket_and_search_tickets_and_merge_and_update_trigger_have_no_op_checks`
lists it alongside three of the other unconstrained tools). And `backend.Backend` — the Protocol
every tool would ultimately have to call through — declares **exactly one method**, `get_ticket`
(`src/csa_zendesk/backend.py`). `create_ticket`, `update_ticket`, `assign_ticket`,
`add_internal_note`, `reply_publicly`, `solve_ticket`, `close_ticket`, `merge_tickets`,
`update_trigger` and `search_tickets` — **ten of the eleven tools this slice built** — have no
backing `Backend` method and cannot reach `_dispatch` at all. Both mutation-tested proofs above prove
the mechanism by installing **fake** gates and **fake** materialised methods
(`monkeypatch.setitem(pol._GATES, "fake_reply", ...)`, `monkeypatch.setitem(pol.tools.TOOLS,
"fake_constrained", ...)`) — not by exercising any of the real eleven beyond `get_ticket`.

**So: the mechanism is proven. The surface is not.** 188 passing tests and 100% coverage describe a
capability/constraint/scope/reach model that is internally consistent and correctly composed against
one real endpoint and several synthetic stand-ins built to shape-match the other ten. They say
nothing about whether `reply_publicly` actually forces `comment.public=true` against a real ticket,
whether `create_ticket`'s two-part constraint actually stops a live create-and-solve, or whether
`merge_tickets` actually gates at `ticket.close` when a real merge call is made — because none of
those tools has anything to call yet. A reader who sees "188 tests, 100% coverage, three composed
controls" and concludes the eleven-tool surface works has been misled by a true statement about the
wrong thing. That is the gap this document exists to name, and it is not closed by more tests against
`FakeBackend` fakes-of-fakes; it is closed by Block 0b (OAuth) and the real `Backend` methods the
tool table is waiting for.

> **Correction (2026-09-18, final review, Minor).** "Several synthetic stand-ins" undercounts: there
> are **three** (`fake_update_ticket`, `fake_reply`/`fake_reply_2`, and `fake_constrained` — all in
> `tests/test_policy.py`). Separately, "whether `merge_tickets` actually gates at `ticket.close`" is
> no longer even the right hypothetical to test against a real backend: the fix wave applied
> alongside this correction gave `merge_tickets` its own `ticket.merge` capability (see Claim 1's
> correction, above). The paragraph's point is unaffected either way: none of it has been checked
> against a real merge call yet.

---

## What this licenses, and what it does not

**Licenses:** proceeding to implement the ten missing `Backend` methods and their `_GATES` entries
with confidence that the enforcement order, the composition of capability/constraint/scope/reach, and
the two-part-constraint pattern (`create_ticket`) are correct and already tested against the exact
call shapes (a constrained `PUT`, a reach-gated reply, a scope-checked write, an admin write with no
subject key yet) they will need to satisfy. It also licenses treating `assign_ticket` vs
`update_ticket` — identical on every impact axis, split only for usability
(`analysis/TOOL-BOUNDARIES.md`, "The two findings from the table") — as a real precedent: bucket
purity permits a usability split inside one bucket but does not require it, and the design needs an
explicit answer for when to take that split before dozens of tools make the question ambiguous.
Filed as **B26**.

**Does not license:** any claim that `create_ticket`, `update_ticket`, `assign_ticket`,
`add_internal_note`, `reply_publicly`, `solve_ticket`, `close_ticket`, `merge_tickets`,
`update_trigger`, or `search_tickets` are enforced against anything real today. Only `get_ticket` is
wired end to end. It does not license the design doc's "30–50 tools" estimate as still current — see
**B28** and the correction below. And it does not license reading `check_boundaries.py`'s `OK` as
"every tool is bucket-pure" without the qualifier that it can only detect impurity between tools that
share an operation — see **B24**.

---

## Filed as a result of this slice

- **B24** *(already filed by Task 1/Ruling 4)* — the checker's blind spot on single-tool operations. This
  slice's read confirms it is the load-bearing residual risk for the 822-operation derivation.
- **B25** *(already filed; now resolved)* — `test_every_profile_only_grants_capabilities_that_exist`
  (`tests/test_policy.py`, added in commit `03e5a43`, Task 5's "carried requirement 3") asserts
  `PROFILES` values are a subset of `ALL_CAPABILITIES`. Marked done below.
- **B26** *(new)* — `assign_ticket` sets a usability-split precedent inside a bucket; the design has
  no rule yet for when to take it.
- **B22** *(already filed; updated, not resolved)* — confirmed still true: `policy.py`'s callable
  `Gate` has no production user. `_GATES` holds one entry and it is a plain string, not a callable;
  the only callable gates in the test suite are synthetic (`test_a_callable_gates_kwargs_reach_it_through_the_wrapper`).
- **B28** *(new)* — the design doc's §4 "30–50 tools" estimate assumed grouping reduces the count;
  ADR-016 replaced grouping with splitting for exactly the case this slice tested, and the observed
  6:1 split on the ticket-write path makes the old estimate unsafe to keep using unqualified.
- **C7** *(new)* — ten of eleven tools have no backing `Backend` method and cannot reach `_dispatch`;
  the enforcement seam is proven by mutation testing against `get_ticket` and synthetic fakes only.
- **C8** *(new)* — `assert_subject_permitted` hardcodes the literal `"ticket_id"` as the subject key;
  `update_trigger` needs `trigger_id` and has no backing method yet, so this is currently fail-closed
  by omission rather than by a real subject-key concept.
- **C9** *(new)* — `test_every_tool_has_exactly_one_capability` checks non-empty/no-space, not true
  singularity (Task 1 minor, deferred).
- **C10** *(new)* — the rewritten `test_no_profile_grants_close_or_raw` dropped four string-literal
  checks (`"ticket.purge"`, `"people.purge"`, `"people.merge"`, `"people.suspend"`) that the retired
  test checked against every profile by value (Task 2 minor, deferred).
- **C11** *(new)* — `tests/test_public_api.py` hard-asserts the module count (`== 9`); same defect
  class as **E10**. *(Corrected 2026-09-18: the assertion in the file is `== 10`, not `== 9`.)*
- **C12** *(new)* — leading-zero ticket ids in an allowlist are accepted as literal strings and can
  never match a canonical Zendesk id — fails closed by accident, with no operator-facing error
  (Task 3 minor, deferred).
- **C13** *(new)* — whitespace-only and comment-only allowlist values collapse silently to the same
  empty state as unset, with no error distinguishing "meant it" from "typo" (Task 3 minor, deferred).
- **C14** *(new)* — `AllowlistError` is defined in `_scope.py` rather than `exceptions.py`, where
  every other typed error lives (Task 3 minor, deferred, the plan's own fault).
- **C15** *(new)* — one inert `# pragma: no cover` in `tests/test_policy.py` (~line 421) on a
  genuinely unreachable return; lives in `tests/`, which the coverage gate never measures, so it has
  zero effect, but it matches a pattern the plan otherwise forbids (Task 5 minor, deferred).

**Added by the 2026-09-18 final review (this correction), filed and largely closed in the same
commit:**

- **C1–C4** *(fixed, not filed open)* — `merge_tickets` under-tabled `reach`, and `update_ticket` /
  `create_ticket` under-forbade `custom_status_id` and the collaborator fields. Corrected in
  `analysis/tool-boundaries.csv` and `src/csa_zendesk/tools.py` in the same commit as this document's
  correction, rather than filed as open TODOs, because the fix was small and mechanical once found.
- **B24** *(re-scoped)* — the predicate was wrong ("solo operations get no check"); the real one is
  "a denylist constraint cannot be verified closed by comparing tools." See the Claim 1 correction
  above and `TODO.md`'s corrected B24 row.
- **C8** *(extended)* — `merge_tickets`' wrong-subject case (checks the surviving target, never the
  tickets closed via `ids` — fail-**open**, unlike `update_trigger`'s existing fail-**closed** case
  below it) is added alongside `update_trigger`'s. **This is not cleared by anything in this
  document.** It is currently exercisable only by calling `assert_subject_permitted` directly, the
  way `TODO.md`'s C8 row does — `merge_tickets` has no `_GATES` entry (see Claim/finding **C7**
  above: ten of eleven tools, `merge_tickets` included, have no backing `Backend` method and cannot
  reach `_dispatch` at all today), so no real call can hit it *yet*. That is an accident of
  sequencing, not a mitigation: the bug lands live the moment `merge_tickets` gets a backing
  `Backend` method and a `_GATES` entry, unless the per-tool subject-key concept `TODO.md` calls for
  ships first. See `TODO.md`'s extended C8 row for the exact scope-bypass example
  (`CSA_ZD_ALLOWLIST_WRITE=44821`, `merge_tickets(ticket_id=44821, ids=[99999])` closes `99999`
  outside the allowlist).
- **I1** *(new; filed and closed in the same commit)* — reach enforcement hung on `_GATES`,
  uncross-checked against `TOOLS`. Closed by two new tests in `tests/test_policy.py`. See `TODO.md`'s
  I1 row.
- **I5** *(fixed)* — `test_no_profile_grants_a_reach_capability` hardcoded `TICKET_REPLY`; it now
  iterates `REACH_CAPABILITIES`, so a second reach capability (`ticket.merge`, added by this same
  correction) can't be silently granted by a profile.

See `TODO.md` for the filed rows.
