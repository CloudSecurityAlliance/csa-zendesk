# WAITING-FOR-002: Required status checks on `main`

**Status:** Open
**Date identified:** 2026-09-08
**Type:** Sequencing — the thing cannot be configured until it has run once

## Waiting for

The three CI job names from `.github/workflows/tests.yml` — `lint`, `test` and `gates` —
to have reported on a pull request at least once, so they can be added to `main`'s
required status checks.

## Why waiting

`main` is protected today with `required_pull_request_reviews` and `enforce_admins`, which
between them stop a direct push and a force-push. But `required_status_checks` is **empty**,
so a pull request whose CI is red can still be merged.

The reason it is empty is mechanical rather than an oversight: GitHub will not let you
require a check context it has never seen. Until a workflow run reports against a PR, there
is nothing to select. Block 0 is the first branch with CI that runs, so this becomes
possible for the first time when Block 0's PR opens.

## Why it is written down rather than remembered

It was deferred once already, during Block 0, on the reasoning that the contexts did not yet
exist and that I would check CI by hand at merge time. That is precisely the shape
[ZERO-DEFECT](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/ZERO-DEFECT.md)
ZD-17 warns about — *if this fired forever, would anyone notice?* A gate enforced by
somebody remembering to look is not a gate, and the whole point of the four CI checks is
that nobody has to.

## Trigger

**Block 0's pull request completes a CI run.** At that point:

```bash
gh api -X PUT repos/CloudSecurityAlliance/csa-zendesk/branches/main/protection/required_status_checks \
  -f strict=true -f 'contexts[]=lint' -f 'contexts[]=test' -f 'contexts[]=gates'
```

Note `test` is a matrix job across Python 3.10–3.13, so confirm whether the reported
contexts are one name or four before setting them; require all of whatever it reports.

## Resolution

Resolved when a pull request with a failing check cannot be merged. Verify by observation,
not by reading the setting back: open a throwaway PR that fails one check and confirm the
merge button refuses. Branch protection has already been misread once in this repo —
`enforce_admins: true` alone was mistaken for blocking pushes, when it only blocks
force-pushes and deletions.
