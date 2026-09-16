# Security Resources

The security surface of this project: what it exposes, to whom, and how it is protected.

**Last reviewed:** 2026-09-16 · **Next review:** 2026-12-16

## Summary

**No network surface.** A Python library and a local stdio MCP server run by an operator on their own
machine — nothing listens, nothing is deployed. The exposure is a credential, a published artifact,
and the thing that makes this repo different from its siblings: **the ability to take an action that
cannot be undone and that a customer sees.**

## Exposure surface inventory

| Surface | Type | Exposure tier | Cloudflare | Auth | Notes |
|---|---|---|---|---|---|
| `github.com/CloudSecurityAlliance/csa-zendesk` | public repo | `public-unauthed` | n/a | none | Source, specs, ADRs. No credentials, no tenant data |
| Local stdio MCP server | process on an operator's machine | `internal-staff` | n/a | inherits the operator's shell | Not network-reachable. No listener |
| PyPI package | published artifact | `public-unauthed` | n/a | n/a | **Not yet published.** `PUBLIC-GITHUB-REPO-STANDARDS.md` applies when it is |
| Zendesk REST API | outbound only | n/a | n/a | OAuth, public client with PKCE ([ADR-009](DECISIONS-ADR/ADR-009.md)) | Acts as the operating user; Zendesk's own roles are the ceiling |
| **A public reply** | **outbound, to a customer** | `public-unauthed` | n/a | n/a | **Irreversible and externally visible. The surface that matters** |

**Cloudflare is not applicable** to any row — no row is a CSA-operated inbound network surface. A
conclusion, not an omission.

## Access model

OAuth with PKCE as a public client, exactly one token file ([ADR-009](DECISIONS-ADR/ADR-009.md)). The
server acts as the operating user, so Zendesk's own role model is the real ceiling and a failure of
our capability layer exposes that agent's own authority — not the whole instance.

Two layers sit above it and both are ours:

- **Toolsets select surface; capabilities grant authority; the operator decides both**
  ([ADR-006](DECISIONS-ADR/ADR-006.md)). The software does not pick a risk appetite on the operator's
  behalf.
- **Gates are drawn on reversibility, not on read-versus-write** ([ADR-003](DECISIONS-ADR/ADR-003.md)).
  An internal note and a public reply are both writes; only one reaches a customer, so only one is
  treated as irreversible. Public replies are their own tool rather than a boolean.

The generic request tool ([ADR-008](DECISIONS-ADR/ADR-008.md)) is the escape hatch and it **refuses
what the curated tools already cover** — otherwise it becomes a route around the gating, which is the
documented failure mode for escape hatches in this fleet.

## Data classification

**Nothing is stored.** Aggregate in memory for the model, export to disk for people, persist neither
([ADR-005](DECISIONS-ADR/ADR-005.md)). There is no `DATA-RESOURCES.md`; that is an explicit N/A and a
deliberate one — a support corpus is the most PII-dense material in the fleet and this project is not
its custodian.

Ticket contents, requester identities and email addresses pass through memory in transit. Ticket
content is **untrusted input**: a requester writes it, so it reaches a model as attacker-influenced
text in the same way mail does, if less deliberately.

## Known gaps and accepted risks

| Gap | Status | Owner |
|---|---|---|
| **Nothing watches the three vendored OpenAPI specs for drift** | Open — [CINO-PE #49](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/issues/49) | Kurt Seifried |
| **No security audit yet.** Tier-1 requires one before 1.0.0; there is one backend method implemented, so this is not yet overdue | Open — gate before 1.0.0 | Kurt Seifried |
| **Six families are unimplemented because they cannot be tested** ([ADR-001](DECISIONS-ADR/ADR-001.md), [WAITING-FOR-001](WAITING-FOR/WAITING-FOR-001.md)) | Accepted — untested code reaching a support queue is worse than no code | Kurt Seifried |
| **`PUBLIC-GITHUB-REPO-STANDARDS.md` not yet applied** | Open — gate before first release | Kurt Seifried |

## Review schedule

Reviewed 2026-09-16. Next review 2026-12-16, or **on the first release that ships a write tool**,
whichever is sooner — the moment the irreversible-action row stops being theoretical.
