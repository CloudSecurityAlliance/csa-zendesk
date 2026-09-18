# WAITING-FOR-003: Zendesk stops issuing API tokens on 2026-10-27

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

Resolved when Block 0b ships and an OAuth flow has authenticated against the live account. Until
then the API tokens minted under action 1 are the whole margin.
Resolved when Block 0b ships and an OAuth flow has authenticated against the live account. The
research scripts are ported to OAuth as a follow-on, not as a precondition.

**Amendment (2026-09-18):** still **Open** — not resolved by this note. What Block 0b has built:
the full OAuth flow (public client, PKCE S256, the localhost-listener callback with a paste
fallback, the token file and its refresh, `whoami`), the reactive refresh-on-`invalid_token` path,
and the three research scripts (`zd.py`, `ui_actions.py`, `probe_families.py`) ported to
authenticate the same way. All of it is unit-tested against a mock transport. What remains: **no
run of any of this has yet gone against the live Zendesk account.** The token file has never been
populated by a real authorization, because that step mints a real credential against a production
tenant and is deliberately held back for a human at a browser rather than done by an agent. The
only outstanding step to close this item is that human-performed live login — after which this
entry should be marked Resolved with the date it happened, not before.
