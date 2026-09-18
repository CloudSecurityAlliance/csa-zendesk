<<<<<<< HEAD:WAITING-FOR/WAITING-FOR-002.md
<<<<<<< HEAD
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
=======
# WAITING-FOR-002: Zendesk stops issuing API tokens on 2026-10-27
=======
# WAITING-FOR-003: Zendesk stops issuing API tokens on 2026-10-27
>>>>>>> 9bd2568 (docs: renumber the API-token deadline to WAITING-FOR-003):WAITING-FOR/WAITING-FOR-003.md

**Status:** Open
**Date identified:** 2026-09-17
**Type:** Deadline — the window closes on a date, whether or not we act

## What changes, and when

| Date | What happens |
|---|---|
| **2026-10-27** | **No account can create a new API token.** Existing tokens keep working |
| **2027-04-30** | **Existing API tokens stop working.** OAuth is the only route |

Source: [ADR-009](../DECISIONS-ADR/ADR-009.md), which records both dates as the forcing facts behind
the OAuth decision. Neither date was tracked anywhere until now — the first appeared in an ADR's
prose, which is the wrong place for a thing that expires.

## Why this is urgent rather than merely known

Today's credential is an API token (`CINO_CSA_ZENDESK`), and it is what makes every read path
testable before OAuth exists. **After 2026-10-27 it cannot be replaced.** Rotate it, lose it, need a
second one for a test account, need one for CI — and the answer is no, permanently.

So the cost of the first date is not "OAuth is late". It is that the interim credential becomes
**irreplaceable rather than merely deprecated**, and every workflow that would have wanted its own
token has to already have one.

## Trigger

**Ship Block 0b before 2027-04-30.** This is a deadline, not a wait, and
[ADR-015](../DECISIONS-ADR/ADR-015.md) removed the option of buying time.

The obvious mitigation — mint every token the project could ever want before 2026-10-27, while that
is still possible — was **considered and declined**. A stockpile buys until 2027-04-30 regardless,
and every token in it would be an argument for not building OAuth this quarter. The deadline is more
useful as a forcing function than as something to insure against.

## What is being accepted

After 2026-10-27 the one existing token is **irreplaceable**. If it is lost, rotated or revoked
before Block 0b ships, `scripts/zd.py`, `scripts/ui_actions.py` and `scripts/probe_families.py` stop
working and the API inventory cannot be refreshed until OAuth exists. Nothing at runtime depends on
them, so this delays research rather than breaking the product.

The mitigation is not a spare token. It is Block 0b shipping.

## Resolution

<<<<<<< HEAD
Resolved when Block 0b ships and an OAuth flow has authenticated against the live account. Until
then the API tokens minted under action 1 are the whole margin.
>>>>>>> a02452d (docs: the API-token window shuts in 40 days, and it was tracked nowhere)
=======
Resolved when Block 0b ships and an OAuth flow has authenticated against the live account. The
research scripts are ported to OAuth as a follow-on, not as a precondition.
>>>>>>> cd4f952 (docs: ADR-015 — OAuth only, and the token window is allowed to close)
