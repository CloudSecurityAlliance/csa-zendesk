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

But read that as *coverage of the specs*, not coverage of the API. The Help Center spec turns out
to describe about 18 of roughly 30 documented families — see §4b. The percentage is honest about
what can be generated; it overstates what is reachable.

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

### Access audit — what this credential actually reaches

`scripts/probe_access.py` walks every family in the inventory, probing one
collection-level GET each and classifying the answer. Run 2026-08-31.

| Verdict | Families | Operations | Share |
|---|---:|---:|---:|
| available | 90 | 733 | 83% |
| needs args (400/422 — endpoint exists, wants parameters) | 5 | 15 | 2% |
| **plan- or feature-gated (403)** | **8** | **44** | **5%** |
| absent (404) | 1 | 3 | <1% |
| untestable — no instance of that type exists to address | 5 | 38 | 4% |
| unmeasured — id-addressed only, mostly Voice and write-only | 16 | 49 | 6% |

**748 of 882 operations (85%) are confirmed reachable.** 47 are refused. The rest are
unmeasured rather than unavailable.

Refused, and worth naming because each is a product boundary rather than a bug:

| Family | Ops | |
|---|---:|---|
| IT Asset Management (assets, types, locations, statuses) | 23 | 403 — an add-on |
| Group SLA Policies | 7 | 403 |
| Workspaces (contextual workspaces) | 7 | 403 |
| Ticket Form Statuses | 4 | 403 — needs non-default custom statuses to exist |
| Audit Logs | 3 | 403 — Enterprise |
| Help Center Service Catalog Items | 3 | 404 |

A 403 here means *authenticated and refused*: a feature boundary, not a credential problem.
The library keeps all of these — another account will have them — but they must fail with a
message that distinguishes "your plan does not include this" from "this is broken".

### Correction: two earlier "not on this account" claims were wrong

An earlier pass reported **Task Lists** and **ITAM Assets** as absent (404 `InvalidEndpoint`).
Both were wrong, for the same reason: **the paths were guessed from memory rather than read
from the operation inventory in this repository.**

| Claimed | Path used | Actual path | Actual result |
|---|---|---|---|
| ITAM Assets absent | `/api/v2/assets` | `/api/v2/it_asset_management/assets` | **403, plan-gated** |
| Task Lists absent | `/api/v2/task_lists` | `/api/v2/tickets/{id}/task_lists` | **200, available** |

The failure mode is worth keeping because it is the same one this document warns about
elsewhere: **a 404 from a guessed path is not evidence of anything.** `analysis/operation-inventory.csv`
is the authoritative path list; probes should be generated from it, which is what
`scripts/probe_access.py` now does.

### Still unmeasured

Sixteen families expose only id-addressed GETs and were not resolved: mostly Voice
(Availabilities, Calls, Callback Requests, Digital lines, IVR Menus, IVR Routes, Recordings),
which is post-1.0 anyway, plus write-only surfaces (Ticket Import, User Passwords, User Images)
and the channel framework. Five more are untestable here because no instance of the type exists
to address — the custom-object sub-resources, and dynamic-content variants.

Unmeasured is not unavailable. See the CSV for the full path list.

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

## 4b. Help Center coverage is narrower than the operation count suggests

**The published Help Center spec is incomplete.** It declares 18 families and 182 operations;
the Help Center API reference documents roughly **30**. The spec is missing about a dozen,
including several that are live on this account.

Probed 2026-08-31 — documented in the reference, absent from the OAS:

| Family | Reachable here | Notes |
|---|---|---|
| Management Permission Groups | **yes** | who may author what; one surveyed server exposes it |
| Themes | **yes** | Guide theming; has its own `themes:read` / `themes:write` scopes |
| Content Tags | **yes** | cross-cutting content tagging, distinct from article labels |
| Redirect Rules | **yes** | empty here |
| Federated Search — external content sources / types / records | **yes** | all empty here; indexes non-Zendesk content into HC search |
| Badges · Badge Categories · Badge Assignments | no (404) | community gamification |
| Guide Media Objects | no (404) | |
| Account Custom Claims · Help Center JWTs | no (404) | JWT-authenticated HC access |

**The six 404s are ambiguous and should not be read as "unavailable".** Most returned a bare 404
with no body, so they are indistinguishable between *not on this plan*, *not enabled*, and *the
path was guessed wrongly* — these are undocumented in the OAS, so the paths came from inference.
Only Help Center JWTs returned the typed `InvalidEndpoint`. Resolving each needs the reference
page for that family, not another guess.

### There is a whole `/api/v2/guide/` namespace

The HC spec declares exactly **three** `/api/v2/guide/` paths — `search`, `user_images`, and
`user_images/uploads`. At least four more answer 200: `permission_groups`, `theming/themes`,
`content_tags`, `redirect_rules`. So `help_center/` and `guide/` are two prefixes over one
product, and only one of them is described.

### Two search endpoints, and the older one is the working one

- `GET /api/v2/help_center/articles/search.json?query=…` — works, offset-paginated envelope
  (`count`, `page`, `page_count`, `per_page`, `next_page`, `results`).
- `GET /api/v2/guide/search?query=…` — **400s** without a `filter` object, and then again
  without `filter[locales]`. A newer, stricter interface that demands filters up front.

Anything built against `guide/search` needs its filter contract established first; the
article-search endpoint is the one to start from.

### What this means for scope

1.0.0 still covers Help Center, but **"182 operations" is the size of the *spec*, not of the
API.** Roughly a dozen families need hand-written backend methods, because there is nothing to
generate them from — the same position the whole project is in for Live Chat and Messaging, just
inside a capability we had assumed was fully described.

The four confirmed-and-empty families (Redirect Rules, and the three Federated Search ones) are
worth deferring on evidence rather than principle: they exist, they are reachable, and nothing
uses them here.

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

### 5.4b Mixing pagination styles returns 200 and silently drops the sort

A request carrying both a cursor page parameter and the older sort parameters is accepted, and
the sort is discarded:

```
sort=updated_at                    & page[size]=3  ->  ascending by updated_at
sort=-updated_at                   & page[size]=3  ->  descending by updated_at
sort_by=updated_at&sort_order=desc & page[size]=3  ->  DEFAULT ORDER - sort ignored
page[size]=3   (no sort at all)                    ->  identical to the line above
sort_by=updated_at&sort_order=desc & per_page=3    ->  correct, under offset paging
```

HTTP 200 throughout. A caller who migrates pagination but not sorting gets plausible data in
the wrong order forever. Zendesk's own PHP client strips `page`, `per_page`, `sort_by` and
`sort_order` before every cursor request, which says the hazard is known internally — see
`OFFICIAL-CLIENTS.md` §2.

**Invariant: never emit both pagination styles on one request. Refuse, rather than trusting
the caller to keep them apart.**

### 5.4c Cursor paging restricts what you can sort by

| | Sortable |
|---|---|
| offset | `assignee`, `assignee.name`, `created_at`, `group`, `id`, `locale`, `requester` |
| cursor | `updated_at`, `id`, `status` |

Verified live: every offset-only attribute returns **400 `InvalidPaginationParameter`** under
cursor paging. Only `id` is common to both.

`created_at` is **not** cursor-sortable, so "the newest tickets" — the most natural request
anyone will make — has no direct expression under the pagination style we default to.
`sort=-id` is the proxy, and translating it is the tool layer's job, not the caller's.

### 5.4d `status: "closed"` is accepted, and it is terminal

An earlier draft of this document asserted that Closed could not be set through the API and was
reachable only by automation some days after Solved. **That was wrong.** The API accepts it:

```
PUT /api/v2/tickets/{id}   {"ticket": {"status": "closed"}}   ->  200
```

And a closed ticket is then **frozen against every subsequent write**:

```
PUT .../{id}  {"ticket": {"status": "open"}}     -> 422  status: "closed prevents ticket update"
PUT .../{id}  {"ticket": {"status": "solved"}}   -> 422  same
PUT .../{id}  {"ticket": {"comment": {...}}}     -> 422  same
```

Reads still work. There is no reopen: the platform's answer to a closed ticket is a follow-up
ticket, so **closing is the single most destructive ordinary operation on a ticket** — more so
than solving, which reverses, and more than a field edit, which is audited.

**Design consequences.**

1. `close` must be gated separately from ordinary writes, and separately from `solve`. Grouping
   it with "update the ticket" would put an irreversible action behind a reversible-sounding
   capability.
2. The tool description must say it cannot be undone. A model that treats `closed` as the
   natural end state of "finish this ticket" will freeze tickets that should have been solved.
3. The agent web interface does **not** offer Closed in its status picker — only New, Open,
   Pending, On-hold, Solved. So this is an action the API permits and the UI declines to expose,
   which is the opposite of the usual direction and worth stating loudly.

### 5.4e `details` is keyed by field name, not always `base`

The required-fields refusal returns `details.base[]`. This one returns `details.status[]`:

```json
{"error":"RecordInvalid","description":"Record validation errors",
 "details":{"status":[{"description":"closed prevents ticket update"}]}}
```

So `details` is a **map from field name to a list of problems**, and `base` is simply the key
used for whole-record problems that belong to no single field. An error parser that reads
`details.base` specifically will silently find nothing on any field-scoped validation error.
**Iterate the keys of `details`; never index one.**

### 5.4f `comment.public` has no fixed default — it inherits from the ticket

Zendesk's ticket-comments reference: *"The initial value set on ticket creation persists for any
additional comment unless you change it."* The OpenAPI schema declares **no default** for the
property, which is consistent — there isn't one to declare.

Verified read-only against two tickets that arrived **via email**, whose first comment is
therefore `public: true`. A subsequent comment omitting the flag inherits that.

This is worse than either fixed default would be:

- **It cannot be known from the request.** Predicting it requires reading the ticket's history.
- **It varies per ticket**, so identical code behaves differently on different tickets.
- **The common case is the dangerous one.** Most tickets arrive by email, so most default to
  public — and a public comment emails the requester and every collaborator.
- **It is invisible in testing.** A tool exercised against an agent-created internal ticket looks
  safe, then emails a customer in production.

**Invariant: never omit `public`.** Either require it explicitly at the tool boundary, or set
`false` explicitly and make publishing opt-in. Omission is the one option that must not exist,
because the resulting behaviour is a property of the ticket rather than of the call — and the
failure is irreversible, since the email has already gone.

*Established from the vendor's documentation plus read-only observation consistent with it. Not
confirmed by writing a comment with the flag omitted: on the available test ticket that would
have emailed four uninvolved people, and the design conclusion is the same whichever way the
mechanism resolves.*

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

### 7.1 Redirect URLs are pre-registered, and the OOB URN is not registrable

The OAuth client form in Admin Center states, verbatim:

> Add the absolute URLs for redirecting users after they authorize access to your app. URLs must be
> absolute (not relative) and use HTTPS, unless they are for localhost or 127.0.0.1. List each URL on
> a new line. For example, `http://localhost` or `http://127.0.0.1`

Three facts fall out of that, and they constrain the auth design:

1. **The list is a fixed set of strings.** Consistent with `specs/zendesk-support-oas.yaml:30858`
   (`redirect_uri` = "an array of the valid redirect URIs for this client"). A dynamically-bound
   loopback port cannot match unless Zendesk implements RFC 8252's any-port rule — still unprobed.
2. **`urn:ietf:wg:oauth:2.0:oob` cannot be registered.** It is not an absolute URL, so the form's own
   validator rejects it. There is therefore **no out-of-band redirect on Zendesk**, and any paste
   fallback must redirect to a registered loopback URI and have the operator copy `code` out of the
   browser's address bar after the connection fails.
3. **Loopback is exempt from the HTTPS requirement**, so no local TLS is needed.

**Use the literal `127.0.0.1`, not `localhost`** — RFC 8252 §8.3. `localhost` resolves through the
host's resolver, so it can be redirected by `/etc/hosts` or DNS, and on a dual-stack machine it
commonly resolves to `::1` first, which never reaches a listener bound to IPv4 `127.0.0.1`. Both are
browser-trusted secure contexts, so nothing is lost by taking the literal. The two forms are **not**
interchangeable to a string-matching authorization server: send byte-for-byte what was registered.

### 7.2 Overlapping scopes are rejected at client registration

Operator-observed, 2026-09-18, registering the `csa-zendesk` client: Admin Center **rejects a scope
list that contains both a broad scope and a narrower member of it** — e.g. `read` together with
`tickets:read`. This is not in the spec text, and it invalidated an earlier recommendation here to
request `read` alongside per-family scopes. Pick one level and stay at it:

- **Broad:** `read` plus the specific writes (`tickets:write`, …) — fewer strings to typo, but grants
  read on every family Zendesk has, including ones deliberately out of scope for 1.0.0.
- **Granular:** `tickets:read tickets:write ticket_views:read …` — the set the server actually needs,
  and the one that makes the token itself the boundary rather than the allowlist.

Granular is the better fit for this project, because **the token's scope is the only control an
attacker cannot reconfigure** — the toolset, capability profile and allowlist are all local settings
(`DECISIONS-ADR/ADR-012.md`). Note this interacts with the §7 typo trap: a granular list is longer,
so it is more likely to contain the unrecognised scope name that silently yields a token that 403s.
Compare requested against granted on every token, without exception.

### 7.2b The registered ceiling, and what it does and does not restrain

Registered 2026-09-18 on the `csa-zendesk` client, verified against the spec's scope table (line
22900ff):

```
read  tickets:write  ticket_attachments:write  ticket_views:write
```

`read` is documented as *"Read all data. Gives access to GET endpoints, including permission to
sideload related resources."* Mixing a broad read with specific writes is explicitly legal — the spec
gives `"organizations:write read"` as an example — so this combination is well-formed despite §7.2.

Two consequences that the capability model, not the token, has to absorb:

1. **`tickets:write` includes DELETE.** The spec defines the write action as *"access to POST, PUT,
   and DELETE endpoints for creating, updating, and deleting resources"*, and resource-specific
   scopes are `resource:action` over the same two actions. So the token cannot distinguish a reply
   from a deletion, and **the reversibility axis of DEC-015 is enforced entirely by us**. Spec-derived
   and unprobed — do not rely on a narrower reading without testing it.
2. **`impersonate` is absent, and must stay absent.** It is the one scope that would break the
   project's founding invariant — *you cannot do anything with this tool that you cannot already do
   in Zendesk* — because it lets an admin act as another user. Its absence is a design decision, not
   an oversight.

**The ceiling is not the grant.** A token request may ask for any subset, and the client's list only
caps it. That is the mechanism for "authority one rung at a time" *without re-registering the
client*: start by requesting `read tickets:write`, and add the two narrower writes when a tool needs
them. Requesting a scope outside the ceiling fails closed with `400 invalid_scope` and issues no
token — unlike a typo'd scope, which fails open with a 403-everything token (§7).

**Token expiration is on and cannot be turned off** (*"This includes existing tokens and cannot be
turned off once turned on"*). Refresh is therefore mandatory rather than an optimisation, and the
refresh token itself defaults to 30 days (line 22799) — an installation left unused for longer needs
a full re-authorisation, not a refresh.

### 7.2c One client, one token per person

The OAuth **client** is an account-level object an admin registers once; the **token** is per user.
The spec states that clients "access the Zendesk API on behalf of users" (line 106), and the token
listing "returns the properties of the tokens for the current user … admins can view OAuth token
properties for all users using the `all` parameter" (line 9640). There is no per-user client, and a
**global** client (line 30838) is the other direction entirely — an app distributed to *other
companies'* Zendesk accounts, requiring Zendesk's approval. Ours is local to the tenant.

This is what actually delivers the project invariant. A token's authority is the intersection of
three things, and only the first is ours to set:

1. the scope requested, capped by the client's ceiling (§7.2b);
2. **the authorising user's own Zendesk permissions** — role, group membership, ticket access;
3. what the tool surface exposes at all.

So `tickets:write` on a light agent's token still cannot reach tickets outside that agent's groups.
The scopes cap what the application may *ask for*; the person caps what the token can *reach*.

**It also decides the ADR-009 question in principle.** If `refresh_token` genuinely requires
`client_secret` (§7.3), that secret would have to be distributed to every operator who runs the
server — at which point it is not a secret, and RFC 8252 §8.4 treats the client as public regardless
of the vendor's terminology. A probe can still tell us what Zendesk *accepts*; it cannot make a
shared, widely-distributed string into a client credential.

### 7.3 Every client gets a secret, including a public one

`secret` is documented as *"Generated automatically on creation and returned in full only at that
time"* (line 30868), so **being handed a secret says nothing about the client's type**. The type is
the separate `kind` field — `"public"` or `"confidential"` (line 30850).

This was unresolved on paper because the spec's own examples disagree: the **authorization-code
example omits `client_secret` and sends `code_verifier`** (line 22805), while its **refresh-token
example sends `client_secret`** (line 22827). Taken at face value that would mean refresh
requires the secret and this client is confidential in practice, contradicting
`DECISIONS-ADR/ADR-009.md`'s "public client, no secret" — so it had to be settled against the
live tenant rather than trusted to either example.

**Settled 2026-09-19, against the live tenant.** A `refresh_token` grant carrying `grant_type`,
`refresh_token` and `client_id` — no `client_secret` — was accepted and returned a new token
pair. The refresh-token example's inclusion of `client_secret` is misleading for a public client;
the authorization-code example's omission is the one that holds. The client is genuinely public
and ADR-009's decision stands (see its 2026-09-19 amendment). Two more behaviours came out of the
same probe and belong here because they shape how a public client's credential should be handled:

- **Refresh tokens rotate.** Every refresh returns a *new* `refresh_token`; the old one is
  consumed. Verified by fingerprinting the token file before and after a refresh — both
  `access_token` and `refresh_token` changed, `scope` was preserved.
- **There is no reuse detection.** Replaying an already-consumed refresh token is refused with
  the standard refusal, but **the successor token continues to work** — the chain is not
  revoked. Verified by deliberately replaying a consumed token and then successfully refreshing
  with its successor. See `TODO.md` E11 for what this means for the missing lock file.

The secret is still retained at `0600` as a matter of course (it is displayed exactly once and
recovering it means rotating the client), but nothing in the live client's behaviour depends on
it — refresh works identically with it absent.

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
