# Tool boundaries — hand-authored, and the rule that checks them

**Date:** 2026-09-17
**Status:** Validation slice, Task 1 (Block 0c). Answers the question ADR-016 raised: given
`(operation × constrained arguments)` as the definition of a tool, does a hand-authored table over a
small, deliberately hard slice actually stay bucket-pure?
**Specified by:** [`ADR-016`](../DECISIONS-ADR/ADR-016.md); table and checker in
`analysis/tool-boundaries.csv` and `scripts/check_boundaries.py`.

Every count below comes from running `scripts/check_boundaries.py`, not from typing a number:

```
$ python3 scripts/check_boundaries.py
OK - 11 tools over 6 operations
```

**Eleven tools, six operations** — one `GET`, one `POST /tickets`, one `POST .../merge`, one `PUT
/triggers/{id}`, and **one `PUT /tickets/{id}` backing six of the eleven tools**: `update_ticket`,
`assign_ticket`, `add_internal_note`, `reply_publicly`, `solve_ticket`, `close_ticket`.

## The table

| tool | method | path | constraint | effect | reversibility | reach | capability |
|---|---|---|---|---|---|---|---|
| `get_ticket` | GET | `/api/v2/tickets/{ticket_id}` | none | read | n/a | internal | `ticket.read` |
| `search_tickets` | GET | `/api/v2/search` | none | read | n/a | internal | `ticket.read` |
| `create_ticket` | POST | `/api/v2/tickets` | no public comment; body must not contain status / custom_status_id / additional_collaborators / email_ccs / followers / collaborator_ids | write | reversible | internal | `ticket.write` |
| `update_ticket` | PUT | `/api/v2/tickets/{ticket_id}` | body must not contain comment or status; nor custom_status_id / additional_collaborators / email_ccs / followers / collaborator_ids | write | reversible | internal | `ticket.write` |
| `assign_ticket` | PUT | `/api/v2/tickets/{ticket_id}` | body may contain only assignee_id or group_id | write | reversible | internal | `ticket.write` |
| `add_internal_note` | PUT | `/api/v2/tickets/{ticket_id}` | comment only; public forced false | note | reversible | internal | `ticket.note` |
| `reply_publicly` | PUT | `/api/v2/tickets/{ticket_id}` | comment only; public forced true | reply | irreversible | contacts-a-person | `ticket.reply` |
| `solve_ticket` | PUT | `/api/v2/tickets/{ticket_id}` | status only; solved | solve | reversible-for-a-period | internal | `ticket.solve` |
| `close_ticket` | PUT | `/api/v2/tickets/{ticket_id}` | status only; closed | close | irreversible | internal | `ticket.close` |
| `merge_tickets` | POST | `/api/v2/tickets/{ticket_id}/merge` | forbid source_comment_is_public and target_comment_is_public | close | irreversible | contacts-a-person | `ticket.merge` |
| `update_trigger` | PUT | `/api/v2/triggers/{trigger_id}` | none | write | reversible | internal | `admin.write` |

`scripts/check_boundaries.py` enforces bucket purity mechanically: every tool has exactly one
capability, no tool sharing an operation with a sibling leaves its constraint at `none`, and no two
tools on the same operation are indistinguishable on `(effect, reversibility, reach, constraint)`.
`tests/test_boundaries.py` pins that behaviour — 4 tests, all passing.

## Correction applied to this table (decided 2026-09-17, before Task 1 started)

The plan's draft constraint for `create_ticket` was `comment.public must be false or absent` alone.
That under-constrains it: `POST /tickets` accepts a `status` field on create, so a single call can
create-and-solve a ticket in one step — spanning the `write` and `solve`/`close` impact buckets the
same way the undifferentiated `PUT` did before ADR-016. The constraint actually written is:

```
no public comment; body must not contain status
```

both halves required, for two independent reasons: a public comment on create emails the requester
(reach), and a `status` in the body collapses two impact buckets into one call (impact).

**This is a second instance of ADR-016's category error, and the mechanical checker does not catch
it.** `check_boundaries.py` only compares tools that share an operation; `create_ticket` has no
sibling on `POST /tickets` in this table, so a `create_ticket` that itself spanned buckets would pass
`OK` silently — the same way the pre-ADR-016 view of `PUT /tickets/{id}` would have, read as a single
row. The checker enforces purity *between* tools sharing an operation; it does not enforce that a
solo tool's own scope stays inside one bucket. That has to come from someone reading the operation's
full field surface by hand — which is exactly what this validation slice was for.

## The two findings from the table (not the design)

**`assign_ticket` shares every axis value with `update_ticket`** — `write` / `reversible` /
`internal`, identical on all three impact axes. What separates them is the constraint alone
(`assignee_id`/`group_id` only, vs. everything except `comment`/`status`). Bucket purity *permits*
this split — the checker passes, because the constraints differ — but does not *require* it; a single
`update_ticket` covering both would also have been bucket-pure. This is a **usability split**, not an
impact split, and the table records it as one so the distinction doesn't get lost and mistaken for a
second impact boundary later.

**`merge_tickets` is not `ticket.write` — reading the axes moved it, correctly, off that bucket.**
Reading the axes rather than the tool's name shows a merge is irreversible and terminal for the
ticket being merged away — not the same bucket as a routine field edit. Filed as **TODO C6**: prior
handling of `merge_tickets` (anywhere it was informally assumed to be a generic write) should be
corrected to gate it beyond `ticket.write`.

**Where it landed is not `ticket.close` either, and that took a second pass to see.** The first
reading of the axes stopped at "irreversible and terminal" and filed `merge_tickets` alongside
`close_ticket` under `ticket.close`. That reading was incomplete: `POST .../merge` also accepts
`source_comment_is_public` / `target_comment_is_public`, so a merge can email the requester the same
way `reply_publicly` does — it carries **reach**, which `close_ticket` does not. Once the reach
invariant existed (every `reach=True` tool's capability must be in `REACH_CAPABILITIES`, and no
profile may grant a `REACH_CAPABILITIES` member — see `src/csa_zendesk/policy.py`), sharing
`ticket.close` with the non-reaching `close_ticket` became unsatisfiable: a profile granting
`ticket.close` for routine closes would, transitively, grant a reach-carrying operation too, and a
profile that couldn't grant `ticket.close` at all (correct for `close_ticket`, which reaches no one)
would wrongly block a merge that has nothing to do with reach in the caller's mental model. The fix
wave gave `merge_tickets` its own capability, `ticket.merge` — distinct from `ticket.close`, in
`REACH_CAPABILITIES`, and granted by no profile, same as `ticket.close`. See
[`DECISIONS-ADR/ADR-010.md`](../DECISIONS-ADR/ADR-010.md)'s correction for the decision-log side of
this, and `analysis/tool-boundaries.csv` / `src/csa_zendesk/tools.py` for the current table.

## A discrepancy the brief's own prose had, caught only by running the checker

Task 1's brief states in prose, ahead of its own CSV block, "Eleven tools over eight operations...
`PUT /tickets/{id}` backs five of them." Grouping the brief's own verbatim CSV by `(method, path)`
gives **six** operations, with the `PUT` backing **six** tools (`update_ticket`, `assign_ticket`,
`add_internal_note`, `reply_publicly`, `solve_ticket`, `close_ticket`) — not eight and five. This
document reports the numbers the checker actually computed (six operations, six tools on the shared
`PUT`), per the brief's own instruction to derive counts by running the script rather than typing
them, rather than repeating the brief's prose summary. This is exactly the failure mode zero-defect
practice exists to catch: a typed number and a computed number silently disagreeing, and the reader
trusting whichever one they saw first.

## Did anything require bending the rule?

**No bend was needed to make the mechanical checker pass.** All eleven tools are bucket-pure under
`(effect, reversibility, reach, constraint)` as scoped to tools that share an operation, and all four
tests plus the checker pass without any row needing a special case, an extra axis, or a relaxed
comparison.

**But the rule as encoded (cross-tool purity on shared operations) is narrower than the rule as
stated in ADR-016 (a tool never spans two impact buckets).** The `create_ticket` correction above is
a real example of a tool that, unconstrained, would have spanned buckets on its own — with no sibling
tool for the checker to compare it against, and therefore nothing for the mechanical check to catch.
The rule held here only because a human re-read the operation's field surface and wrote the second
half of the constraint by hand before the checker ever ran. That is a finding worth carrying into
the full 822-operation derivation: bucket purity within a shared operation is mechanically checkable
from this table alone; bucket purity of a single unshared tool against the *operation's own* field
surface is not, and needs either a second checker pass over `operation-classification.csv`'s
per-field detail or continued hand review at each solo-operation tool.
