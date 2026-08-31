# csa-zendesk

```
project_tracker_base: CINO Project Tracker:appf7fRQUvY9Iy7sL
project_tracker_table: Projects:tblchmbxSAavvJKaY
project_tracker_record: csa-zendesk:recvtmgqPccgLvuXz
project_source: github:CloudSecurityAlliance-Internal/CINO-Projects/projects/CloudSecurityAlliance/csa-zendesk
```

A Python library and local stdio MCP server over the Zendesk REST API, targeting **100% API
coverage**.

> **Status: API surface enumerated. Nothing implemented.** There is no `src/` yet. This
> repository currently holds the upstream API snapshots, the operation inventory, and the probe
> findings that will constrain the design. Do not describe any feature below as working.

## Scope

| Capability | Operations | 1.0.0 |
|---|---:|---|
| Ticketing | 640 | yes |
| Help Center | 182 | yes |
| Status | 3 | yes |
| Voice (Talk) | 60 | post-1.0 |
| Live Chat, Messaging, AI Agents, Custom Data, Sales CRM | no published spec | post-1.0 |

**825 of 882 machine-readable operations at 1.0.0** — which is coverage of the published
specs, not of the API. The Help Center spec describes roughly 18 of ~30 documented families;
about a dozen, several of them live, have no spec entry and need hand-written methods. See
`analysis/API-SURFACE.md` §4b.

## Why this exists

The third-party Zendesk MCP servers are thin. The most popular — 115★, actively maintained,
genuinely well built — ships **7 tools**. The largest ships ~47. Against 882 operations.

More consequentially, the whole field shares four weaknesses:

- **No capability policy.** OAuth scopes, fixed at client registration, are the only control.
  Zendesk has **54 granular scopes**, which gives a local fail-closed policy layer something real
  to bind to.
- **Offset pagination everywhere.** Zendesk caps offset paging at 100 pages / 10,000 records and
  then returns HTTP 400. Large result sets truncate, and the tools present the truncation as an
  answer.
- **API-token auth.** Zendesk **permanently deactivates all API tokens on 2027-04-30**, and no
  account can create a new one after 2026-10-27.
- **Pre-2.0 MCP SDK.** Hand-rolled `list_tools`/`call_tool` dispatch means no structured output
  and no tool annotations, so a client cannot tell a read from a destructive write.

## Architecture

Third in the line after [`csa-skilljar`](https://github.com/CloudSecurityAlliance/csa-skilljar)
and `csa-google-workspace`, on the same spine:

```
Backend (Protocol)   the seam - keyword-only args, returns raw upstream envelopes
    ^ wrapped by
PolicyBackend        capability gating; FAILS CLOSED - an ungated method is refused
    ^ consumed by
ZendeskClient        thin typed library surface (the public product)
    ^ consumed by
mcp/_tools/*.py      per-family register_*(app, get_client) producers
```

Enforcement lives in the wrapper around the seam, not in the tools, so a library embedder gets
the same guarantee an MCP client does.

## What is here

| Path | What |
|---|---|
| `specs/` | Three upstream OpenAPI snapshots + `PROVENANCE.md` (URLs, sha256) |
| `analysis/API-SURFACE.md` | **Start here.** The enumeration and the probe findings |
| `analysis/operation-inventory.csv` | 882 rows, one per operation |
| `analysis/family-probe.json` | 49 live availability probes |
| `scripts/inventory.py` | Regenerates the inventory from `specs/` |
| `scripts/probe_families.py` | Re-runs the live probes (GET only) |
| `scripts/probe_access.py` | Access audit: what the credential actually reaches |
| `TODO.md` | **The index of all open work.** Start here for what is unfinished |
| `DECISIONS-ADR.md` | Decision log index; entries in `DECISIONS-ADR/` |
| `WAITING-FOR.md` | Conditions with observable triggers; entries in `WAITING-FOR/` |

Zendesk publishes the OpenAPI specs but links to none of them; all three were found by probing
URL shapes. They are snapshots of someone else's moving target — re-fetch and diff before
trusting them.

## Findings that constrain the design

Six are recorded in `analysis/API-SURFACE.md`. The three that change how the code must be
written:

1. **`users/me.json` returns HTTP 200 with `"name": "Anonymous user"` when wholly
   unauthenticated.** It serves anonymous Help Center visitors, so it degrades instead of
   refusing — and it is the endpoint every client uses as a credential health check. Validating
   against it reports healthy for a missing credential. *Credential validation probes a resource
   endpoint and asserts a non-null `user.id`.*
2. **Search takes offset pagination only and stops at 1000 results**, while reporting a `count`
   of six figures. A tool that surfaces `count` as if the caller could page to it is lying.
   `search/export` is the cursor-paginated, uncapped alternative.
3. **Pagination cannot be generated from the specs.** The Help Center spec declares paging on 0
   of 96 GET operations, yet cursor paging demonstrably works. The generator must not read
   silence as "unpaginated".

## Deliberate exclusions

Six families — 47 operations, 5% of the surface — are **out of scope and will not be built**:
IT Asset Management, Group SLA Policies, Workspaces, Ticket Form Statuses, Audit Logs, and
Help Center Service Catalog Items. The development account cannot reach them (403: a plan
boundary), so they cannot be tested, and this project does not ship API code it has never
called. See [ADR-001](DECISIONS-ADR/ADR-001.md) for the reasoning and
[WAITING-FOR-001](WAITING-FOR/WAITING-FOR-001.md) for what would reopen it.

This does not relax the error layer: plan boundaries differ per account, so any deployment can
meet a 403 on an endpoint we *did* implement, and the taxonomy must say "your plan does not
include this" rather than "this is broken".

## Configuration

Credentials come from `./.env`, which is gitignored:

| Variable | What |
|---|---|
| `CINO_CSA_ZENDESK` | API token (basic auth as `EMAIL/token:TOKEN`) |
| `CINO_CSA_ZENDESK_EMAIL` | The account the token is paired with |

The API token is a **stopgap**. It is unscoped, carries full admin rights, bypasses account 2FA,
and Zendesk retires it on 2027-04-30. The shipped design authenticates with OAuth.

```bash
set -a; . ./.env; set +a
python3 scripts/inventory.py         # 882 operations
python3 scripts/probe_families.py    # 43/49 families reachable
```

## License

Apache 2.0.
