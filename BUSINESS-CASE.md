# Business Case

## Executive summary

Zendesk's API exposes roughly 885 operations across ticketing, Help Center, Status and Voice. Zendesk
ships an official MCP server; the third-party ones are surveyed in `other-zendesk-mcp-servers/`.
Between them they cover the read-and-reply path and stop there.

The gap is not obscure endpoints. It is that **an agent that can genuinely work a support queue has
to write** — set fields, apply macros, merge, reassign, update Help Center articles — and writing to
a support system is where the risk lives, because **a public reply cannot be unsent**. Every existing
option answers that by either not offering the writes or offering them undifferentiated.

This project offers them, gated on **reversibility rather than read-versus-write**, with irreversible
operations verified by observation rather than by performing them. That gating is the product.

**Benefit categories:** Synergy (primary) · Required / Compliance (secondary) · Brand (secondary)

## CSA value

**Support triage becomes scriptable, and the support corpus becomes answerable.** Ticket analysis,
recurring-issue detection and Help Center maintenance are currently manual or unexported.

**It feeds Customer 360.** Zendesk is one of the mirrored source systems; a typed client with a
stable contract is useful to the data foundation independently of the MCP surface.

**It is the fleet's write-safety reference.** Zendesk is the first sibling whose *ordinary* operation
is irreversible and reaches a real person. The reversibility-based gating designed here is what the
others inherit when they grow write surfaces — `csa-google-workspace` already ships destructive tools
present-and-off, and that question gets harder as more of the fleet writes.

## Why not the alternatives

| Alternative | Why not |
|---|---|
| **Zendesk's official MCP server** | Covers the common read-and-reply path. The long tail of ticketing and Help Center administration is absent, and there is no capability layer an operator can narrow |
| **Third-party Zendesk MCP servers** | Surveyed in `other-zendesk-mcp-servers/` and `analysis/PRIOR-ART.md`. Same shape, same ceiling, and none distinguishes a reversible write from an irreversible one |
| **The official Zendesk SDKs** | Client libraries, not agent surfaces, and [ADR-002](DECISIONS-ADR/ADR-002.md) rejects chasing parity with them — they solve a different problem and carry surface we do not want |
| **Scripting the REST API directly** | Works, and gets none of the gating. Which is the point: the library is the product and the gate is at the data seam, so a script written against the library inherits the same refusals an MCP client gets |

## The security component is the product

Support data is the most PII-dense material in the fleet, and support actions are the most visible to
people outside CSA. Four decisions carry the weight:

- **Capability gates drawn on reversibility** ([ADR-003](DECISIONS-ADR/ADR-003.md)). Read-versus-write
  is the wrong axis when an internal note and a public reply are both writes and only one of them
  reaches a customer. Public replies are their own tool, not a flag.
- **Irreversible operations verified by observation, not by performing them**
  ([ADR-004](DECISIONS-ADR/ADR-004.md)).
- **Capabilities derived from a classification of every operation**
  ([ADR-010](DECISIONS-ADR/ADR-010.md)), because a hand-maintained gate table drifts from the surface
  it gates and the tests derived from it agree with it while both are wrong.
- **Aggregate in memory, export to disk, persist neither** ([ADR-005](DECISIONS-ADR/ADR-005.md)).
  This project is not the custodian of a support corpus.

Ten ADRs were settled before any tool was written. That is unusual, and for a surface where a mistake
is a message to a customer, it is the right order.

## Operational burden

Low. A library and a local stdio server; no deployment, no stored tickets, no index. The recurring
cost is watching the three vendored OpenAPI specs for drift, which nothing currently does.

## AI enablement

The whole point is an agent working a queue rather than a human pasting between tabs — and the
gating exists so that can be trusted incrementally rather than all at once. An operator picks a
toolset and a capability set ([ADR-006](DECISIONS-ADR/ADR-006.md)); the software does not choose
their risk appetite for them.
