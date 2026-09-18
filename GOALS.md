# Goals

Shared goals for CSA's MCP server fleet — library-first, local stdio, one fail-closed seam at the
data boundary, offline-testable, published with attestations — are stated once in the fleet roster
(`surfaces/mcp/ROSTER.md` in the internal CINO-Platform-Engineering repo) and are not restated here.
This file records only what is specific to Zendesk.

Ten ADRs were settled before any tool was written. Where a goal below restates one, it links to it
rather than re-arguing it.

## North Star

CSA's support operation — tickets, comments, users, organizations, Help Center content — is
drivable by scripts and AI agents, with **the blast radius of every call visible before it is made**.

Zendesk is the first server in the fleet where the ordinary operation is *irreversible and reaches a
customer*. A public reply cannot be unsent. That single fact sets the shape of the whole project:
capability gates are drawn on **reversibility rather than on read-versus-write**
([ADR-003](DECISIONS-ADR/ADR-003.md)), public replies are their own tool rather than a flag on a
comment, and irreversible operations are verified **by observation rather than by performing them**
([ADR-004](DECISIONS-ADR/ADR-004.md)).

## Near-term

| Goal | Success metric |
|---|---|
| ~~Land Block 0~~ | **Done 2026-09-17** ([#12](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/12)). The typed error hierarchy, pagination guard, HTTP client, `Backend` seam with an offline `FakeBackend`, fail-closed capability policy, and `get_ticket` reaching through every layer |
| ~~Land Block 0c — the tool-surface validation slice~~ | **Done 2026-09-18** ([#23](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/23)). 11 tools over 6 operations, 204 tests, three controls composed at one seam. It was an experiment and it failed usefully — see [`analysis/SLICE-FINDINGS.md`](analysis/SLICE-FINDINGS.md) |
| **Block 0b: authentication** | Public OAuth client with PKCE and exactly one token file ([ADR-009](DECISIONS-ADR/ADR-009.md)). Nothing is callable against a real Zendesk until this lands — literally, since [ADR-015](DECISIONS-ADR/ADR-015.md) removed the API token path. **Hard deadline 2027-04-30**, when existing tokens stop working ([WAITING-FOR-003](WAITING-FOR/WAITING-FOR-003.md)) |
| **Capabilities derived, not hand-listed** | Every operation classified, and the capability set generated from that classification ([ADR-010](DECISIONS-ADR/ADR-010.md)). A hand-maintained gate table drifts from the surface it gates and tests itself against its own assumptions |
| **The gate is proven by refusal** | For each capability, a test that the gate *refuses* — run one capability at a time against a hand-written expectation, not one derived from the gate table. **Partly met:** Block 0c proved the seam refuses on all four grounds (capability → constraint → scope → reach) by *mutation* — breaking each control and confirming a test fails. Ten of eleven tools have no backing `Backend` method, so the mechanism is proven and the surface is not |

## Medium-term

- **Ticketing and Help Center to completeness** — 640 and 182 operations respectively, plus Status.
  Voice is post-1.0.
- **The escape hatch, with a conscience.** A generic request tool that **refuses what the curated
  tools already cover and names the remedy** ([ADR-008](DECISIONS-ADR/ADR-008.md)) — so the library's
  reach is not capped by the tool list, without the hatch quietly becoming the way everything is done.
- **Async work surfaced, not reinvented.** Zendesk's own job system is exposed rather than wrapped in
  a bespoke one ([ADR-007](DECISIONS-ADR/ADR-007.md)).
- **Bulk reads that do not become a data store.** Aggregate in memory for the model, export to disk
  for people, **persist neither** ([ADR-005](DECISIONS-ADR/ADR-005.md)). A support corpus is the most
  PII-dense thing in the fleet and this project is not its custodian.

## Long-term

- **Testable access to the six excluded families.** They are unimplemented because they cannot be
  tested, not because they are unwanted ([WAITING-FOR-001](WAITING-FOR/WAITING-FOR-001.md), a cost
  trigger). When a sandbox with those families exists, [ADR-001](DECISIONS-ADR/ADR-001.md)'s
  exclusion expires on its own terms.
- **Useful outside CSA.** Any Zendesk customer gets the same capability, free and open source.

## Non-goals

Named so they are decisions rather than drift.

- **Families this project cannot test** ([ADR-001](DECISIONS-ADR/ADR-001.md)). Untested code that
  reaches a customer's support queue is worse than no code.
- **Parity with the official Zendesk SDKs** ([ADR-002](DECISIONS-ADR/ADR-002.md)). The client is
  built from scratch against the OpenAPI specs; matching someone else's surface is not a goal.
- **Persisting ticket data anywhere.** No cache, no local store, no index.
- **Choosing the operator's risk appetite for them.** Toolsets select surface, capabilities grant
  authority, and **the operator decides both** ([ADR-006](DECISIONS-ADR/ADR-006.md)).

## How we would know this failed

1. **A destructive call is made that the operator did not know was destructive.** The whole
   reversibility-based gate design exists for this, and it is the failure that reaches a real
   customer rather than a log.
2. **The generic request tool becomes the way things are done.** If callers route around the curated
   tools, the curation was wrong — and the gate coverage it was meant to preserve is gone.
3. **The capability table is hand-maintained again.** It will drift, and the tests derived from it
   will agree with it while both are wrong.
4. **Ten ADRs and no server.** The design is unusually well-settled for a project with one backend
   method implemented; that asymmetry is a risk, not an achievement.

## Who benefits

- **CSA** — support triage, ticket analysis and Help Center maintenance become scriptable, and the
  support corpus becomes answerable without exporting it.
- **The community** — a public, from-scratch Zendesk client with a fail-closed capability layer,
  which the official SDKs do not provide.
- **The fleet** — this is the first sibling whose ordinary operation is irreversible and
  customer-facing, so its reversibility-based gating is the pattern the others inherit when they
  grow write surfaces.
