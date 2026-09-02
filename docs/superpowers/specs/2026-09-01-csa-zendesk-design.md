# csa-zendesk — design

**Date:** 2026-09-01 · **Status:** proposed, nothing implemented

The authoritative design. It assembles eight decisions already taken (`DECISIONS-ADR/`) into one
architecture, and adds the module layout, the tool surface and the build order. It decides
nothing new; where it appears to, that is a defect and should be raised.

Read `analysis/API-SURFACE.md` first — this document assumes its findings.

---

## 1. What is being built

A Python library (`csa_zendesk`) and a local stdio MCP server over the Zendesk REST API.

**The library is the product; the MCP server is its first consumer.** That ordering matters:
everything the server can do is reachable from Python, and the enforcement guarantees hold for
both.

### Scope

| Capability | Operations | 1.0.0 |
|---|---:|---|
| Ticketing | 640 | yes |
| Help Center | 182 | yes |
| Status | 3 | yes |
| Voice | 60 | post-1.0 |
| Live Chat, Messaging, AI Agents, Sales CRM | no published spec | post-1.0 / out |

Minus six families excluded by ADR-001 as unreachable and therefore untestable (44 operations),
and noting that the Help Center *spec* describes roughly 18 of ~30 documented families — so
"825 machine-readable operations" is coverage of the specs, not of the API (`API-SURFACE.md`
§4b).

**Reachable and in scope: ~700 operations.**

---

## 2. Architecture

```
        ┌─ ZendeskClient ──────────── thin, typed, the library's public surface
        │
        ├─ PolicyBackend ─────────── capability gating. FAILS CLOSED.
        │                             gate may be a function of the call's kwargs
        │
        └─ Backend (Protocol) ────── the seam. keyword-only args.
             │                        returns RAW upstream envelopes, unshaped
             ├─ ApiBackend ────────── real HTTP
             └─ FakeBackend ───────── in-memory, powers every unit test

        mcp/_tools/*.py ──────────── per-family register_*(app, get_client, settings)
                                      shapes envelopes into TypedDicts
                                      writes the model-facing contract in docstrings
```

Three properties are load-bearing and must survive any rework:

1. **The seam returns raw envelopes.** Shaping belongs to the delivery layer. This is what lets
   one uniform wrapper gate every method, and it is why ADR-002 rejects every object mapper.
2. **Enforcement wraps the seam.** Not a check in the tools. A library embedder gets the same
   guarantee an MCP client does.
3. **The gate table fails closed.** A `Backend` method with no entry is *refused*, not delegated.
   Forgetting to declare one turns a feature off rather than leaving a hole, and a test asserts
   the protocol and the table have not drifted.

### Why not an existing library

ADR-002. No official Python SDK exists. `zenpy` is untyped, caches by default, and its object
mapper occupies the seam we deliberately left empty. `python-zendesk` is generated from the spec
we have repeatedly caught being wrong, is Support-only, and does not model `details.base[]`.

What we take from them instead (`analysis/OFFICIAL-CLIENTS.md` §7): the cursor-capable path list,
never emitting both pagination styles, detecting pagination style from the response, reading
`details` first on a validation error, and treating `503` as retryable.

---

## 3. Authority and surface

Two orthogonal dimensions (ADR-006). The field conflates them; separating them is a deliberate
differentiator.

### Toolsets — does the tool exist?

Operator config at launch. No runtime enabling.

`context` (always on) · `tickets` · `help_center` · `people` · `queues` · `reporting` ·
`export` · `admin`

**Default: `context` + `tickets`.** Instructions are per-toolset and compose based on what else
is enabled, so a deployment without `reporting` never spends context on aggregation guidance.

### Capabilities — may an existing tool act?

Ordered by reversibility (ADR-003). The default profile is everything that can be undone.

| Capability | Action | Reversible? | Default |
|---|---|---|---|
| `ticket.read` | read tickets, comments, audits | n/a | yes |
| `ticket.note` | internal note | yes | yes |
| `ticket.write` | fields, assignee, tags, form | yes, audited | yes |
| `ticket.reply` | **public** comment | **no** — emailed | no |
| `ticket.solve` | set solved | for a window, then no | no |
| `ticket.close` | set closed, **and merge** | **no** | **no profile** |
| `hc.read` / `hc.write` | Help Center | — / yes | yes / no |
| `people.read` / `people.write` | users, orgs, groups | — / yes | yes / no |
| `reporting.read` | aggregation | n/a | yes |
| `reporting.export` | write files to disk | n/a | no |
| `admin.read` / `admin.write` | triggers, automations, SLA, fields | — / yes | no / no |
| `raw.read` / `raw.write` | the escape hatch | — / varies | **no profile** |

A registered tool whose capability is not granted **remains visible and refuses**, naming what an
operator would change. That is a better failure than an absent tool.

**`merge` is a closing operation.** Observational sampling found merges are a significant
real-world path to the irreversible state (ADR-004), so `merge_tickets` gates at `ticket.close`,
not `ticket.write`.

---

## 4. The tool surface

Derived from what the agent web interface offers (`analysis/UI-ACTION-MAP.md`), not from the
API's shape. **47 tools.** Six names are fixed by the ecosystem survey and must not be
renamed: `get_ticket`, `create_ticket`, `update_ticket`, `get_user`, `get_organization`,
`get_ticket_comments`. Naming is bare `verb_noun` throughout.

| Toolset | Tools | Capability |
|---|---|---|
| **context** *(always registered)* | `whoami`, `describe_capabilities`, `describe_ticket_form` | none |
| **tickets** | `get_ticket`, `get_ticket_comments`, `get_ticket_audits`, `list_tickets`, `search_tickets`, `get_ticket_attachment` | `ticket.read` |
| | `create_ticket` | `ticket.write` |
| | `update_ticket` | `ticket.write` (+`ticket.note` with a comment, +`ticket.solve` when solving) |
| | `take_ticket`, `mark_ticket_as_spam` | `ticket.write` |
| | `add_public_reply` | `ticket.reply` |
| | `close_ticket`, `merge_tickets` | `ticket.close` |
| | `create_side_conversation` | `ticket.reply` |
| | `get_job_status` | `ticket.read` |
| **queues** | `list_views`, `get_view_tickets`, `list_macros`, `describe_ticket_actions` | `ticket.read` |
| | `apply_macro` | `ticket.write` |
| **people** | `get_user`, `search_users`, `get_organization`, `list_group_memberships` | `people.read` |
| **help_center** | `search_articles`, `get_article`, `list_sections`, `list_categories`, `list_translations` | `hc.read` |
| | `create_article`, `update_article`, `update_translation` | `hc.write` |
| **reporting** | `summarise_tickets`, `summarise_ticket_metrics`, `summarise_satisfaction`, `get_ticket_metrics`, `list_satisfaction_ratings` | `reporting.read` |
| **export** | `export_tickets`, `export_search` | `reporting.export` |
| **admin** | `list_triggers`, `list_automations`, `list_sla_policies`, `list_ticket_fields`, `list_ticket_forms` | `admin.read` |
| **escape hatch** *(always registered, see below)* | `zendesk_read` | `raw.read` |
| | `zendesk_request` | `raw.write` |

`create_side_conversation` gates at `ticket.reply` rather than `ticket.note`: a side conversation
sends email outward, which is the property `ticket.reply` names.

**The escape-hatch tools belong to no toolset and are always registered**, like `context`. That is
deliberate and follows §3's principle — a visible tool that refuses informatively is better than
an absent one, and their whole purpose (ADR-008) is that a model can *attempt* something unusual
and be told what to enable. Their capabilities are granted by no profile, so out of the box they
are visible and refuse.

### `update_ticket` is deliberately broad, and cannot go public

The web UI stages field edits, the comment and the status change and applies them in **one
`PUT`**. One tool matching that is faithful to both the UI and the API. The exception is
outward-facing text: `update_ticket`'s comment is **always internal**; `add_public_reply` is the
only path to a public comment (ADR-003), because annotations are per-tool and one tool cannot
honestly advertise both a routine field edit and an irreversible email.

Its gate is therefore **a function of its kwargs**: `ticket.write`, plus `ticket.solve` when
`status == "solved"`. All required capabilities are checked before anything is written.

---

## 5. Cross-cutting behaviour

Every item here is probe-verified and every one fails *silently* if got wrong.

### Pagination

- **Cursor by default.** Detect the style from the response (`meta` + `links`), never from what
  was asked — some endpoints answer cursor-shaped regardless.
- **Never emit both styles on one request.** With a cursor page parameter present,
  `sort_by`/`sort_order` are silently discarded and a 200 is returned with default ordering.
  The backend **refuses** the combination rather than trusting the caller.
- **Cursor restricts sorting** to `updated_at`, `id`, `status`. `created_at` is not
  cursor-sortable, so "newest tickets" is translated to `sort=-id` by the tool layer.
- **Offset dies at 10,000 records** with a typed `InvalidPaginationDepth`.
- **Search is offset-only and caps at 1000** while reporting a far larger `count`. Tools must
  never present that count as reachable; `search/export` is the cursor-paginated alternative.

### Errors

Four incompatible envelope shapes. The parser tries all of them and **iterates the keys of
`details`** — it is a map from field name to problems, so `details.base[]` for whole-record
issues and `details.<field>[]` for field-scoped ones. A parser hardcoded to `base` finds nothing
on a field-scoped error.

Typed hierarchy, finer than the official clients', which collapse everything outside 404/422 into
a network error:

| Condition | Type |
|---|---|
| 401, or a resource read returning an anonymous body | `CredentialsRejected` |
| 403 | `PlanBoundary` — *your plan*, not an outage |
| 404 `InvalidEndpoint` | `EndpointNotAvailable` |
| 404 `RecordNotFound` | `NotFound` |
| 422 `RecordInvalid` | `ValidationError`, carrying the parsed `details` |
| 400 `InvalidPaginationDepth` / `InvalidPaginationParameter` | `PaginationError` |
| 422 search response limit | `SearchLimitExceeded` |
| 429, 503 | `RateLimited`, `ServiceUnavailable` — both retryable |

**Credential validation probes a resource endpoint and asserts a non-null `user.id`.** Never
`users/me`, which answers 200 with an "Anonymous user" object when wholly unauthenticated.

### Rate limits

Layered: an account limit plus much tighter per-endpoint buckets (incremental export is an order
of magnitude tighter). Honour both header families and `Retry-After`; default to 10s when absent.
Retry `429` and `503`. Never retry a non-idempotent write on `5xx` — the mutation may have landed.

### Untrusted content

Prompt injection through ticket bodies is the named primary risk. Zendesk-origin text is wrapped
in generated delimiters before it reaches the model, applied **at the boundary** rather than
per-tool, on by default. Aggregation (§6) is the strongest mitigation on the bulk path, because
content is counted rather than read aloud.

---

## 6. Bulk and async

**Aggregate for the model, export for people, persist neither** (ADR-005). `summarise_*` streams
pages, folds each into an aggregate and discards it; scope is mandatory and an unbounded request
is refused; the result reports how much was sampled. `export_*` writes a file for a human to an
operator-configured directory, and there is no query tool over exports.

**Surface Zendesk's async; invent none** (ADR-007). Bulk writes return a job id. A bulk tool
submits, polls briefly so short jobs return an answer rather than a receipt, then falls back to
the handle; `get_job_status` retrieves it later. **Always report per-record outcomes** — a job
that half succeeded must never read as success. We hold no job state.

---

## 7. Testing

Three tiers (ADR-004):

- **unit** — every path against `FakeBackend`, offline, gates CI. A conformance test reflects
  over the `Backend` protocol and asserts `FakeBackend` and `ApiBackend` have not drifted from it
  or from the gate table.
- **integration** — reads and reversible writes, live, opt-in.
- **observational** — irreversible operations, verified by measuring the platform's own instances
  of them, read-only. Must report *insufficient evidence* distinctly from *pass*.

Behaviour only `ApiBackend` has — pagination, retry, error translation — needs stub-service tests,
not `FakeBackend` tests. That is the one blind spot of the fake/real seam.

---

## 8. Module layout

```
src/csa_zendesk/
  __init__.py          public API and __version__
  _http.py             requests, auth, retry, rate-limit buckets
  _errors.py           envelope parsing -> typed hierarchy
  _pagination.py       style detection, cursor/offset, the both-styles refusal
  backend.py           Backend protocol · ApiBackend · FakeBackend
  policy.py            capabilities, profiles, _GATES, PolicyBackend
  client.py            ZendeskClient
  auth.py              OAuth (authorization_code + PKCE); API token, deprecated
  _content.py          untrusted-content wrapping
  _aggregate.py        streaming folds for summarise_*
  exceptions.py
  mcp/
    server.py          create_server(get_client, settings)
    _config.py         env -> Settings -> thread-local client provider
    _toolsets.py       toolset membership and composed instructions
    _schemas.py        TypedDicts (from typing_extensions below 3.12)
    _tools/            one module per toolset
```

Generated code (ADR-002) lands in `backend.py` in two tiers: **verified** methods, which have
tests against live behaviour and may be exposed; and **generated** methods, present, untested,
not exposed, and labelled as such.

---

## 9. Build order

Each block ends green, with tests, and is a PR.

| Block | Content | Proves |
|---|---|---|
| **0** | package skeleton, CI (lint, types, tests, coverage, security), `_http`, `_errors`, `_pagination`, `Backend` + `FakeBackend`, `policy` skeleton, `get_ticket` end to end | the whole vertical on one method |
| **1** | `context` toolset; ticket read path — comments, audits, list, search; content wrapping | pagination and injection wrapping under real shapes |
| **2** | ticket write path — `create_ticket`, `update_ticket`, `add_public_reply`, `close_ticket`, `merge_tickets`; the capability ladder; kwargs-dependent gates | **the safety-critical block** |
| **3** | rest of `tickets`; `queues`; attachments; `get_job_status` and the bulk/async path | ADR-007 |
| **4** | `people`; `help_center` | the second capability, and the spec-less HC families |
| **5** | `reporting` — `summarise_*`; observational tests | ADR-004 and ADR-005 |
| **6** | `admin`; escape hatch; `export` | ADR-008 and the covered-path refusal |

Blocks 0–2 are the minimum useful server: read a ticket, work it, reply, solve — under a
capability policy that refuses what it has not been granted.

---

## 10. Known open questions

Tracked in `TODO.md`; none blocks Block 0.

- **C1** the requirements precheck over-reports on conditional forms; it matched a live 422 only
  on a form with no conditional rules.
- **C2** `agent_conditions` is undocumented; `required_on_statuses.type` is not enumerated.
- **A2** six Help Center families return ambiguous 404s from inferred paths.
- **A3** the `guide/search` filter contract.
- **A4** whether to depend on an injection-wrapping library or implement the pattern.
- **A6** whether Help Center localisation is real work here — 17 translation operations rest on it.
- **B8** a queryable mirror, post-1.0, which would supersede ADR-005 rather than extend it.
