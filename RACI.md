# RACI

| Role | Who |
|---|---|
| **Responsible** | Kurt Seifried |
| **Accountable** | Kurt Seifried |
| **Consulted** | CSA support staff, as the users of the tools rather than a standing body |
| **Informed** | Anyone installing the server; `security@cloudsecurityalliance.org` for anything in [`SECURITY.md`](SECURITY.md)'s scope |

One name in the first two rows is the accurate description, not a placeholder — this is a
build-for-yourself-first project in the sense CINO-Platform-Engineering
[`BUILD-FOR-YOURSELF-FIRST.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/BUILD-FOR-YOURSELF-FIRST.md)
means. Naming a committee that does not meet would read as answered, which is worse.

## What the concentration costs, which is the useful half

Three things depend on one person, and each has a different mitigation:

- **The literal half of `check_public_safe.py`** runs only on a machine holding the gitignored
  `tenant-config/private-terms.txt`. That is one laptop. CI runs the structural tier only and
  says so. See [`OPERATIONAL-RESOURCES.md`](OPERATIONAL-RESOURCES.md) — this is the one with no
  second copy **by design**, so it cannot be mitigated by backup, only by someone else being
  given the file.
- **Judging a `check_boundaries.py` / `check_controls.py` / `check_spec.py` failure.** Mitigated
  structurally: each rule explains itself where it fires, and `check_public_safe.py` now carries
  a `self_test()` that breaks every rule on purpose.
- **Knowing which tenant facts are private.** Partly in
  [`SECURITY-RESOURCES.md`](SECURITY-RESOURCES.md)'s data classification, partly in that
  gitignored file, and partly unwritten. The unwritten part is the real single point.

## Escalation

- **A security issue** → GitHub Private Vulnerability Reporting, or
  `security@cloudsecurityalliance.org`. [`SECURITY.md`](SECURITY.md) is the policy; it is
  reachable by an external reporter without any CSA access, which is the property that matters.
- **Anything else** → an issue on this repo.

## Decisions

Technical decisions are in [`DECISIONS-ADR.md`](DECISIONS-ADR.md), and the fleet decisions this
project inherits rather than re-making are in
[`DECISIONS-INHERITED.md`](DECISIONS-INHERITED.md). The split exists so that "we decided this"
and "CSA decided this and we comply" are distinguishable — which is what stops a local decision
quietly contradicting a fleet one.
