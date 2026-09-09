# csa-zendesk — design

**Date:** 2026-09-01 · **Revised:** 2026-09-08 · **Status:** proposed, nothing implemented

> Revision 2 folds in a self-review (twelve findings) and two later decisions —
> [ADR-009](../../../DECISIONS-ADR/ADR-009.md) authentication, and
> [ADR-010](../../../DECISIONS-ADR/ADR-010.md) the capability model derived from a
> classification of all 822 in-scope operations.

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
`export` · `admin` · `status`

**Default: `context` + `tickets`.** Instructions are per-toolset and compose based on what else
is enabled, so a deployment without `reporting` never spends context on aggregation guidance.

### Capabilities — may an existing tool act?

`<domain>.<tier>`, derived from a classification of every in-scope operation
(ADR-010, evidence in `analysis/operation-classification.csv`) rather than from the ticket
workflow alone.

| Domain | Tiers | Default profile |
|---|---|---|
| `ticket` | `read` · `note` · `write` · `reply` · `solve` · `close` · `delete` · `purge` | read, note, write |
| `people` | `read` · `write` · `suspend` · `merge` · `delete` · `purge` | read |
| `hc` | `read` · `write` · `delete` | read |
| `admin` | `read` · `write` · `delete` | read |
| `reporting` | `read` · `export` | read |
| `raw` | `read` · `write` | none |

**Cross-cutting: `bulk`.** Required **in addition** to the domain capability for any `_many`,
`/bulk` or `/import` operation. Granting the power to delete one ticket does not grant the power
to delete a thousand.

**Granted by no profile:** every `.purge`, `ticket.close`, `people.merge`, `raw.read`, `raw.write`.

Recoverability across the 822 in-scope operations: 425 reads, 269 reversible, 108 recoverable
with effort, **13 irreversible, 7 irreversible and permanent**. The strictest gates cover twenty
operations, which is why precision here costs almost nothing in configuration burden.

A registered tool whose capability is not granted **remains visible and refuses**, naming what an
operator would change. That is a better failure than an absent tool.

Three re-gatings worth calling out:

- **`mark_ticket_as_spam` is `people.suspend`, not `ticket.write`.** Its full name is *"Mark Ticket
  as Spam and Suspend Requester"* — it suspends a user account, and an earlier draft had it in the
  default profile.
- **`merge_tickets` is `ticket.close`** — ADR-004's observational sampling established that merging
  closes the source ticket.
- **`admin.write` covers 225 configuration operations**, 27% of the surface. Every tool using it
  states in its description that the change affects all future tickets. Splitting it per object
  type is deferred (`TODO.md`), since `admin` is entirely off by default in 1.0.

## 4. The tool surface

Derived from what the agent web interface offers (`analysis/UI-ACTION-MAP.md`), not from the
API's shape. Six names are fixed by the ecosystem survey and must not be
renamed: `get_ticket`, `create_ticket`, `update_ticket`, `get_user`, `get_organization`,
`get_ticket_comments`. Naming is bare `verb_noun` throughout.

| Toolset | Tools | Capability |
|---|---|---|
| **context** *(always registered)* | `whoami`, `describe_capabilities`, `describe_ticket_form` † | none |
| **tickets** | `get_ticket`, `get_ticket_comments`, `get_ticket_audits`, `list_tickets`, `search_tickets`, `get_ticket_attachment`, `get_job_status` | `ticket.read` |
| | `create_ticket`, `take_ticket` | `ticket.write` |
| | `update_ticket` | `ticket.write` (+`ticket.note` with a comment, +`ticket.solve` when solving) |
| | `add_public_reply`, `create_side_conversation` | `ticket.reply` |
| | `close_ticket`, `merge_tickets` | `ticket.close` |
| | `mark_ticket_as_spam` | **`people.suspend`** |
| **queues** | `list_views`, `get_view_tickets`, `list_macros`, `describe_ticket_actions` | `ticket.read` |
| | `apply_macro` | `ticket.write` |
| **people** | `get_user`, `search_users`, `get_organization`, `list_organizations`, `search_organizations`, `list_group_memberships` | `people.read` |
| **help_center** | `search_articles`, `get_article`, `list_sections`, `list_categories`, `list_translations` | `hc.read` |
| | `create_article`, `update_article`, `update_translation` | `hc.write` |
| **reporting** | `get_ticket_metrics` *(one ticket)*, `list_satisfaction_ratings` | `reporting.read` |
| | `summarise_tickets`, `summarise_ticket_metrics`, `summarise_satisfaction` *(all aggregate over a scope)* | `reporting.read` |
| **export** | `export_tickets`, `export_search` | `reporting.export` |
| **admin** | `list_triggers`, `list_automations`, `list_sla_policies`, `list_ticket_fields`, `list_ticket_forms` | `admin.read` |
| **status** | `get_zendesk_incidents`, `get_zendesk_maintenance`, `get_zendesk_incident` | **none** |
| **escape hatch** *(always registered)* | `zendesk_read` | `raw.read` |
| | `zendesk_request` | `raw.write` |

† `describe_ticket_form` computes what a ticket needs in order to be solved. That computation is
**known to over-report on forms with conditional rules** (`TODO.md` C1) — it matched a live 422
exactly, but on a form with zero conditions, so the match validated only the unconditional half.
Since `context` is always registered, this ships in every deployment: the tool description must
state that the list is a superset on conditional forms and that the API's refusal is authoritative.

**54 tools** (counted from the table by `scripts/check_spec.py`, because the figure has been
wrong twice by hand). `summarise_*` aggregate over a scope and return a table; `get_ticket_metrics` takes
one ticket id. Both exist because the questions differ.

**The `status` toolset is unlike every other.** Its three endpoints are on **`status.zendesk.com`**,
a different host, and require **no authentication** — so `_http` needs a second base URL and a
credential-free path, and these tools work before any OAuth flow has run. They are not among the
882 inventoried operations, which are all on the tenant host.

**`create_side_conversation` is not in any published spec.** It is reachable here (probed: 200)
but absent from all three OpenAPI snapshots, like the ~12 undocumented Help Center families. Its
`Backend` method is hand-written, and see the covered-path note below.

**`take_ticket` is a deliberate exception to ADR-003's rejection of preset composites.** ADR-003
turned down `solve_ticket`/`reopen_ticket` as *"the same `PUT` with a preset"*. `take_ticket` is
`update_ticket(assignee_id=<me>)` — but "me" is not expressible by a caller without a `whoami`
round-trip, and self-assignment is the single most common agent action. It earns its place on
those grounds and no others; further preset composites do not.

**`create_ticket_comment` is deliberately not provided**, despite being one of the incumbent
server's seven tools. ADR-003 splits commenting into `update_ticket` (always internal) and
`add_public_reply`, because one tool cannot carry two honest annotations. Anyone arriving from
another server will find the capability, under two names, with the visibility decision made
explicit rather than defaulted.

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

Most items here are probe-verified, and each is marked. Those that are not are inherited
practice from the sibling projects, and say so — the distinction between probed and assumed is
load-bearing for this project's credibility.

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

### Authentication (ADR-009)

**Public OAuth client, `authorization_code` with PKCE (S256), no client secret** — the only
correct option for a local stdio server, and fully supported. *(Probe-verified: Zendesk's own MCP
discovery advertises `token_endpoint_auth_methods_supported: ["none"]` and `S256`.)*

Access tokens are short-lived — 30 minutes by default for clients created on or after
2026-04-30 — so **exactly one artifact is persisted: a token file** at
`$XDG_CONFIG_HOME/csa-zendesk/tokens.json`, mode `0600` in a `0700` directory, written atomically
under a lock. It holds the refresh token, the access token and its expiry, and nothing else.

That amends ADR-005: **no response persistence, no attachment cache — and one token file, because
refresh tokens require it.** A credential is the category `SECURITY.md` already protects; customer
data is the category ADR-005 keeps off disk. Conflating them produced a contradiction across three
documents.

Refresh happens before expiry and on rejection, retried once — **only** on `401` with
`invalid_token`. A `401`/`403` from insufficient scope or from the operator's own Zendesk
permissions passes through unchanged, so a permissions problem stays visible as one.

API tokens ship as a deprecated path (`CINO_CSA_ZENDESK` + `CINO_CSA_ZENDESK_EMAIL`), warn once,
and are overridden by OAuth. They stop working on 2027-04-30.

### Rate limits

Layered: an account limit plus much tighter per-endpoint buckets (incremental export is an order
of magnitude tighter). Honour both header families and `Retry-After`; default to 10s when absent.
*(Probe-verified.)*

- **`429` — always retryable.**
- **`503` — retryable for idempotent requests only.**
- **Never retry a non-idempotent write on any `5xx`**, `503` included: the mutation may already
  have landed. *(Inherited practice from `csa-google-workspace`, not probed here.)*

### Untrusted content

*(Design position, not a probe finding.)* Prompt injection through ticket bodies is the named
primary risk. Zendesk-origin text is wrapped
in generated delimiters before it reaches the model, applied **at the boundary** rather than
per-tool, on by default. Aggregation (§6) is the strongest mitigation on the bulk path, because
content is counted rather than read aloud.

---

### The escape hatch's covered-path table

ADR-008 says `zendesk_read` / `zendesk_request` refuse any path a curated tool already covers.
**That table is built from the tool registry's declared paths, not from the operation inventory.**

ADR-008 originally keyed it on the inventory, which is wrong: the inventory is incomplete — side
conversations and roughly a dozen Help Center families are absent from all three specs — so any
curated tool whose endpoint is not inventoried would leave a hole the escape hatch could route
through, defeating the refusal's whole purpose. The registry knows what it covers; the inventory
only knows what Zendesk documented.

Each `Backend` method therefore declares its `(method, path template)`, a test asserts every
curated operation is refused by the hatch, and the refusal names the tool to use instead.

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
  auth.py              OAuth public client + PKCE; API token, deprecated
  _tokenstore.py       the one persisted artifact: 0600 file, atomic write under a lock
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
| **0** | package skeleton, CI (lint, types, tests, coverage, security), `_http`, `_errors`, `_pagination`, `Backend` + `FakeBackend`, `policy` skeleton, **API-token auth**, `get_ticket` end to end | the whole vertical on one method |
| **0b** | **OAuth: public client, PKCE, the token store, refresh** (ADR-009); `whoami` | the credential path we actually ship |
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
- Whether `admin.write` should split per object type — 225 operations behind one gate. Deferred
  because `admin` is off by default in 1.0 (ADR-010).
- Whether `bulk` should be complemented by a **magnitude threshold** above which an operation
  requires confirmation. One surveyed server does this; `bulk` gates authority, a threshold would
  gate scale, and they are not the same question.
- **A6** whether Help Center localisation is real work here — 17 translation operations rest on it.
- **B8** a queryable mirror, post-1.0, which would supersede ADR-005 rather than extend it.
