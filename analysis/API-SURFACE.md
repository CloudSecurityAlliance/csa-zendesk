# The Zendesk API surface

What exists, what this account can reach, and what `csa-zendesk` covers at 1.0.0.

Compiled 2026-08-30 from three OpenAPI snapshots in `specs/` plus 60-odd live
read-only probes against `the CSA tenant`. Regenerate the
tables with `scripts/inventory.py`; re-run the probes with
`scripts/probe_families.py`.

**Probe beats docs.** Where the published spec and a live probe disagreed, the
probe won and the finding is recorded in §5. That happened four times, and two
of them are silent-failure shapes.

---

## 1. Method

| Source | What it gave us |
|---|---|
| `specs/*.yaml` (3 files, 2.3 MB) | 882 operations, 601 paths, 125 families, 635 schemas |
| Live probes (GET only, 49 families) | Which families this account can actually reach, and how they behave |
| Prose docs | Rate limits, the API-token retirement timeline, OAuth scopes, Status API |

Zendesk publishes the specs but does not advertise them. They are not linked
from the API reference; all three were found by probing URL shapes:

```
https://developer.zendesk.com/zendesk/oas.yaml        Support      640 ops
https://developer.zendesk.com/help_center/oas.yaml    Help Center  182 ops
https://developer.zendesk.com/voice/oas.yaml          Voice         60 ops
```

No spec exists for AI Agents, Messaging, Custom Data, Live Chat, or Sales CRM.
Those families are documented in prose only, and several live on different
hosts entirely.

---

## 2. Capability map — all of Zendesk

Per <https://developer.zendesk.com/api-reference/>, with our disposition.

| Capability | Spec? | Ops | Reachable here | 1.0.0 |
|---|---|---:|---|---|
| **Ticketing** | yes | 640 | yes | **in scope** |
| **Help Center** | yes | 182 | yes | **in scope** |
| **Status** | no (3 documented) | 3 | yes, unauthenticated | **in scope** |
| Voice (Talk) | yes | 60 | partly — 1 phone number, some 404s | post-1.0 |
| Live Chat | no | ? | no — 404 on this host; separate API | post-1.0 |
| Messaging / Sunshine Conversations | separate repo | ? | untested | post-1.0 |
| AI Agents | no | ? | untested | post-1.0 |
| Custom Data (Sunshine) | partly in Ticketing | — | yes, 0 objects defined | post-1.0 |
| Omnichannel | partly in Ticketing | — | yes, 0 queues | post-1.0 |
| Sales CRM | no | ? | separate product, not owned | out of scope |

**1.0.0 = 825 of 882 machine-readable operations (94%), plus the 3 Status
endpoints.** The 57 deferred are Voice.

---

## 3. Ticketing — 90 families, 640 operations

Full per-operation detail: `analysis/operation-inventory.csv`.
`probe` is the live status where a representative GET was issued; `-` means not
probed, not that it is unavailable.

### Reachable and populated on this account

| Family | Ops | R/W | Probe | Note |
|---|---:|---|---|---|
| Tickets | 35 | 21/14 | 200 | **a six-figure total tickets** (search count) |
| Users | 32 | 18/14 | 200 | |
| Macros | 22 | 15/7 | 200 | |
| Views | 20 | 13/7 | 200 | |
| Organizations | 19 | 11/8 | 200 | |
| Skill Based Routing | 18 | 9/9 | 200 | 0 attributes configured |
| Tags | 16 | 6/10 | 200 | offset paging only |
| Triggers | 15 | 8/7 | 200 | trigger categories |
| Groups | 12 | 9/3 | 200 | |
| Ticket Fields | 12 | 6/6 | 200 | many custom fields |
| Ticket Forms | 12 | 4/8 | 200 | several forms |
| Automations | 9 | 4/5 | 200 | |
| Brands | 9 | 4/5 | 200 | a brand |
| Suspended Tickets | 9 | 2/7 | 200 | |
| Custom Ticket Statuses | 7 | 2/5 | 200 | the five built-in statuses |
| SLA Policies | 7 | 3/4 | 200 | 0 policies |
| Custom Objects | 6 | 3/3 | 200 | 0 defined |
| Dynamic Content | 6 | 3/3 | 200 | 0 items |
| Trigger Categories | 6 | 2/4 | 200 | 21 |
| Custom Roles | 5 | 2/3 | 200 | custom roles |
| Ticket Audits | 5 | 4/1 | 200 | |
| Satisfaction Ratings | 4 | 3/1 | 200 | |
| Job Statuses | 3 | 3/0 | 200 | async bulk results |
| Search | 3 | 3/0 | see §5.2 | offset only, 1000-result ceiling |

Also reachable, empty: Targets, Sharing Agreements, Resource Collections,
Omnichannel Routing Queues, Deletion Schedules, Bookmarks. Webhooks: 5
configured. Apps: 4 installed.

### Refused on this account

| Family | Ops | Probe | Why |
|---|---:|---|---|
| Audit Logs | 3 | **403** | Enterprise-plan feature. Not a scope problem. |
| Task Lists | 9 | **404** | `InvalidEndpoint` — in the spec, not on this account |
| ITAM Assets (+ Types/Fields/Locations/Statuses) | 28 | **404** | same |

**37 spec operations are unreachable here.** They stay in the library — the
spec declares them and another Zendesk account may have them — but they must
fail with a message that distinguishes *"your plan does not include this"* from
*"this is broken"*.

### Not probed (49 remaining families)

Group Memberships (14), Organization Memberships (14), User Identities (14),
Requests (12), User Fields (11), Object Triggers (10), Custom Object
Permissions (9), Sessions (8), Custom Object Fields (7), Dynamic Content Item
Variants (7), Group SLA Policies (7), Incremental Export (7), OAuth Clients (7),
Ticket Comments (7), Workspaces (7), Attachments (6), Brand Agents (6), Locales
(6), Organization Fields (6), Organization Subscriptions (6), Support Addresses
(6), Task List Templates (6), and 27 smaller ones. See the CSV.

---

## 4. Help Center — 18 families, 182 operations

Every family probed returned 200. The knowledge base is live at
`support.cloudsecurityalliance.org`.

| Family | Ops | R/W | Probe |
|---|---:|---|---|
| Content Subscriptions | 26 | 13/13 | — |
| Articles | 20 | 10/10 | 200 |
| Votes | 19 | 9/10 | — |
| Translations | 17 | 10/7 | — |
| Sections | 14 | 6/8 | 200 |
| Categories | 12 | 4/8 | 200 |
| Article Attachments | 11 | 8/3 | — |
| Article Comments | 11 | 5/6 | 200 |
| Article Labels | 9 | 4/5 | 200 — labels |
| User Segments | 9 | 6/3 | 200 — segments |
| Post Comments | 7 | 4/3 | — |
| Posts | 7 | 4/3 | — |
| Help Center Search | 6 | 6/0 | 200 — hits |
| Topics | 5 | 2/3 | — |
| Service Catalog Items | 3 | 3/0 | — |
| User Subscriptions | 3 | 1/2 | — |
| User Images | 2 | 0/2 | — |
| Help Center Sessions | 1 | 1/0 | — |

---

## 5. Probe-verified behaviour — the findings that constrain the design

### 5.1 `users/me.json` returns 200 for an unauthenticated caller

```
no credentials      GET /api/v2/users/me.json   200  {"user":{"name":"Anonymous user",...
wrong password      GET /api/v2/users/me.json   200  {"user":{"name":"Anonymous user",...
wrong API token     GET /api/v2/users/me.json   401  Couldn't authenticate you
no credentials      GET /api/v2/tickets.json    401  Couldn't authenticate you
```

It serves anonymous Help Center visitors, so it degrades rather than refusing —
and it is the endpoint every client reaches for as a health check. A credential
check that calls it and tests `status == 200` **reports healthy for a missing
credential**.

**Invariant: credential validation probes a resource endpoint, never
`users/me`, and asserts a non-null `user.id`.**

### 5.2 Search takes offset pagination only, and stops at 1000 results

```
search + page[size]=2                    400  "page must be an integer"
search + per_page=2                      200
search page=100 per_page=10 (=1000)      200
search page=101 per_page=10 (=1010)      422  "Requested response size was greater
                                              than Search Response Limits"
search/export + page[size]=2             200  ← cursor works here
```

`count` reports the true total (six figures) while only the first 1000 are
retrievable. **A tool that reports `count` as if the caller could page to it is
lying.** `search/export` is the cursor-paginated, uncapped alternative and is
the right primitive for anything bulk.

### 5.3 Help Center supports cursor pagination the spec never declares

The Help Center spec declares pagination on **0 of 96** GET operations. Live:

```
GET /api/v2/help_center/articles.json?page[size]=2
  → keys: articles, links, meta
    meta: {has_more: true, after_cursor: "aQAA…", before_cursor: "aQAA…"}
```

The Support spec is no better — 48 of 333 GETs declare paging. **Pagination
cannot be generated from the specs.** It must come from probing, and the
generator must not infer "unpaginated" from silence.

### 5.4 Offset pagination dies at 10,000 records, with a typed code

```
GET /api/v2/tickets.json?page=1001&per_page=10
  400 {"errors":[{"code":"InvalidPaginationDepth", ...}]}
```

Cursor and offset both work on every core list endpoint tested (tickets, users,
organizations, groups, views, macros, triggers, automations, tags,
ticket_fields). **Cursor is the default we ship**; offset is reachable only when
a caller asks for it explicitly.

### 5.5 Errors arrive in three incompatible envelopes

```
{"error": {"title": "Forbidden", "message": "..."}}          object
{"error": "RecordNotFound", "description": "Not found"}      string + description
{"errors": [{"code": "...", "title": "...", "detail": "..."}]}  array
```

Plus a bare `{"error":"Couldn't authenticate you"}`. **No single parse works.**
The taxonomy has to try all four and must never assume a shape — and the
distinctions that matter are semantic, not numeric:

| Status | Body | Means |
|---|---|---|
| 401 | `Couldn't authenticate you` | credential bad or absent |
| 403 | `Forbidden … contact the account owner` | plan does not include this |
| 404 | `InvalidEndpoint` | endpoint not on this account |
| 404 | `RecordNotFound` | record absent |
| 400 | `InvalidPaginationDepth` | offset depth exceeded |
| 422 | `Search Response Limits` | past the 1000-result ceiling |

`InvalidEndpoint` vs `RecordNotFound` under the same 404 is the pair that
matters most: one is "your plan", the other is "your id".

### 5.6 Rate limits are layered, and the headers say so

```
x-rate-limit: 400                ratelimit-remaining: 384   ratelimit-reset: 50
zendesk-ratelimit-tickets-index:        total=20000; remaining=19999; resets=42
zendesk-ratelimit-incremental-exports:  total=10;    remaining=9;     resets=2
```

The account limit is **400/min** on this plan. Endpoint buckets are separate and
much tighter — incremental exports really are **10/min**. A limiter that only
watches `x-rate-limit` will trip the incremental bucket 40× sooner than it
expects. Both header families must be honoured, and `Retry-After` on 429.

---

## 6. What is not machine-readable

- **No `x-` extensions anywhere.** The specs carry no scope annotations, no
  rate-limit hints, no plan-availability markers. csa-skilljar's local scope
  pre-check has no equivalent input here; a scope→endpoint table must be built
  by hand or by probing.
- **The specs will not load with `yaml.safe_load`.** Trigger-condition examples
  contain a bare `=` scalar, which YAML 1.1 resolves to `tag:yaml.org,2002:value`.
  `scripts/inventory.py` carries the one-line constructor. The file is not corrupt.
- **9 of 640 Support operations have no description.** Tool docstrings are
  hand-written regardless — they are the model-facing contract — but the specs
  cannot supply them.

---

## 7. Auth

Working today: **API token**, basic auth as `EMAIL/token:TOKEN`, from
`CINO_CSA_ZENDESK` + `CINO_CSA_ZENDESK_EMAIL` in `./.env`. Verified against
`/api/v2/tickets.json` (not `users/me` — see §5.1). The credential is an
**unscoped admin token**: full read and write everywhere, and it bypasses the
2FA enabled on the account.

Zendesk is retiring API tokens:

| Date | Change |
|---|---|
| 2026-07-28 | tokens unused 30 days auto-deactivate; new accounts cannot create them |
| **2026-10-27** | **no account can create new API tokens** |
| 2027-04-30 | all API tokens stop working permanently |

OAuth supports `authorization_code`, `refresh_token`, and `client_credentials`,
with **54 granular scopes** (`tickets:read`, `hc:write`, `auditlogs:read`,
`impersonate`, …). Trap: *Zendesk issues a token for an unrecognised scope name
and then 403s every request made with it* — so a typo produces a credential that
looks valid and works for nothing. Any scope handling must compare requested
against granted.

---

## 8. Re-checking this document

```bash
# refresh the snapshots, then diff
curl -s -o specs/zendesk-support-oas.yaml     https://developer.zendesk.com/zendesk/oas.yaml
curl -s -o specs/zendesk-help-center-oas.yaml https://developer.zendesk.com/help_center/oas.yaml
curl -s -o specs/zendesk-voice-oas.yaml       https://developer.zendesk.com/voice/oas.yaml
python3 scripts/inventory.py                  # 882 operations as of 2026-08-30

set -a; . ./.env; set +a
python3 scripts/probe_families.py             # 43/49 families reachable as of 2026-08-30
```
