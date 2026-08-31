# WAITING-FOR-001: Testable access to the six excluded families

**Status:** Open
**Date identified:** 2026-08-31
**Type:** Cost — the capability exists and is purchasable; we have not bought it

## Waiting for

An account this project can test against that includes the six families
[ADR-001](../DECISIONS-ADR/ADR-001.md) excluded: IT Asset Management (23 operations),
Group SLA Policies (7), Workspaces (7), Ticket Form Statuses (4), Audit Logs (3), and
Help Center Service Catalog Items (3).

## Why waiting

Nothing about these is hard. They are ordinary REST families and the generator would emit
their `Backend` methods almost for free. What is missing is the ability to *check* — no
account to call, no payloads to shape `TypedDict`s from, no live suite to gate them.

Building them anyway would mean authoring a `FakeBackend` from a specification that has
already proved wrong about pagination on every Help Center GET, about error envelope shapes
four separate times, and about which paths exist. The tests would then confirm the guess.
That is a worse outcome than the gap, because the gap is visible and the wrong fake is not.

None of the six is in use at the reference account, and none is load-bearing for the
workloads this project exists to serve, so the cost of waiting is close to zero.

## Trigger

**Any one of these, all observable:**

1. `python3 scripts/probe_access.py` reports a verdict other than `plan-gated` for any of
   the six families — i.e. the account's plan changed. Re-run it after any Zendesk plan
   change; that is the whole check.
2. Someone with an account that includes them offers to contribute the family *and* a live
   test run against their own tenant.
3. A concrete internal need appears for one of them. **Audit Logs is the likeliest**: "who
   changed what in Zendesk, and when" is a question a security organisation eventually asks,
   and it is three operations. A stated need plus an account to test against would justify
   the family on its own, without waiting for the other five.

## Next action

None until triggered. If trigger 1 fires, ADR-001 needs a superseding decision rather than a
quiet edit — the exclusion is recorded, so its removal should be too.

## Notes

Trigger 1 is deliberately mechanical. "Check whether our plan changed" is the kind of
condition nobody remembers to check, so it is expressed as a script that already exists and a
verdict it already prints, rather than as a diary entry.
