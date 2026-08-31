# What Zendesk's own clients know

Review of the two officially-maintained clients — `zendesk_api_client_rb` (Ruby) and
`zendesk_api_client_php` (PHP) — read 2026-08-31. Clones in `other-zendesk-libraries/`,
gitignored.

These matter more than the community libraries and arguably more than the OpenAPI spec.
They are written by people with internal knowledge, and they encode what Zendesk itself
believes about its API — including several things the spec does not say and one thing the
spec contradicts.

Neither is a candidate dependency (wrong language). They are read as **evidence**.

---

## 1. The pagination guide is the most valuable document either repo contains

`CBP_UPGRADE_GUIDE.md` in the PHP client — Zendesk's own offset-to-cursor migration guide.

**Offset pagination is on the way out.** *"OBP is quite inefficient … OBP will eventually be
subject to limits."* Vendor signalling, not inference.

**Switching to cursor pagination silently changes what you may sort by**, and the two sets
barely overlap:

| | Sortable attributes |
|---|---|
| Offset (OBP) | `assignee`, `assignee.name`, `created_at`, `group`, `id`, `locale`, `requester` |
| Cursor (CBP) | `updated_at`, `id`, `status` |

Only `id` is common to both. Verified live against the tickets endpoint — every OBP-only
attribute returns **HTTP 400 `InvalidPaginationParameter` / "sort is not valid"** under cursor
paging; all three CBP attributes return 200.

Two consequences we have to design around:

- **`created_at` is not cursor-sortable.** "The newest tickets" is the most natural request
  anyone will make, and the obvious field for it does not work under the pagination style we
  intend to default to. `sort=-id` is the proxy, since ids ascend with creation — but that is
  an implementation detail no caller should have to know, so the tool layer must translate it.
- **The sort spelling changes too**: `sort_by=updated_at&sort_order=desc` becomes
  `sort=-updated_at`.

**Parallel page fetching must become serial.** Page N+1 needs page N's cursor. Any bulk
operation is therefore inherently sequential, which bounds how fast we can ever drain a large
result set.

## 2. A silent-failure the guide does not mention, and the PHP client defends against

If a request carries **both** a cursor page parameter and the old sort parameters, Zendesk
returns **HTTP 200 and silently discards the sort**:

```
sort=updated_at            & page[size]=3   ->  [75023, 107057, 114915]   ascending
sort=-updated_at           & page[size]=3   ->  [157529, 157267, 157532]  descending
sort_by=updated_at&sort_order=desc & page[size]=3
                                            ->  [50387, 60224, 61060]     ← neither
page[size]=3   (no sort at all)             ->  [50387, 60224, 61060]     ← identical
sort_by=updated_at&sort_order=desc & per_page=3
                                            ->  [157529, 157267, 157532]  correct under OBP
```

The mixed request returns **default cursor order**, not the requested order, with no warning.
A caller who migrates their pagination but forgets to migrate their sorting gets plausible
data in the wrong order, indefinitely, with a 200.

This is the third instance of this shape in this API, after `users/me` answering 200 for an
unauthenticated caller and search reporting a count far beyond what it will return.

**Zendesk's own PHP client defends against it.** `CbpStrategy::unsetObpParams()` strips
`page`, `per_page`, `sort_by` and `sort_order` before every cursor request. That is strong
evidence the hazard is known internally. (Its docblock says OBP params are "converted to CBP";
the code deletes them. Even the official client's comments drift from its code.)

**For us:** the backend must never emit both styles on one request, and should refuse to,
rather than trusting callers to keep them apart.

## 3. Which endpoints support cursor pagination — the list the spec omits

The Ruby client carries `cbp_path_regexes` per resource, and paginates by cursor only when the
request path matches. This is a vendor-maintained answer to the question the OpenAPI spec
cannot answer, since it declares pagination on 48 of 333 Support GETs and none of 96 Help
Center GETs.

Declared CBP-capable: `tickets` (plus organization- and user-scoped ticket lists),
`organizations`, `organizations/{id}/subscriptions`, `users`, `organizations/{id}/users`,
`groups`, `groups/assignable`, `groups/{id}/memberships`, `brands`, `tags`, `activities`,
`ticket_fields`, `ticket_metrics`, `tickets/{id}/audits`, `suspended_tickets`,
`deleted_tickets`, `views`, `triggers`, `triggers/active`, `automations`, `macros`,
`community/topics`, `oauth/clients`.

**Declared as having no CBP at all: `Search`.** Independently confirming our probe — search
rejects `page[size]` with a 400 and is offset-only.

The PHP client models the same question from the other end: **CBP is its default**, and only
four resources override it as single-page — `Targets`, `SharingAgreements`, `CustomRoles`,
`AppInstallations`. Our own probing agrees: each returned a complete unpaginated collection.

Two caveats. Both lists are Support-centric and partial — the Ruby client declares 22 classes
against our 125 families — and neither covers Help Center meaningfully. So these confirm and
seed, they do not complete.

## 4. Detect cursor support from the response, not the request

```ruby
def cbp_response?(body)
  !!(body["meta"] && body["links"])
end
```

And a comment worth keeping:

> *"this is to cater for CBP responses where we don't specify `page[size]` but the endpoint
> responds CBP by default"*

So some endpoints return a cursor envelope whether or not you asked. **Pagination style is a
property of the response, discoverable at runtime** — which is the only reliable approach,
given that neither the spec nor any hand-maintained list is complete.

The PHP client goes further and **raises** when a response it expected to be cursor-shaped is
not: *"Response not conforming to the CBP format"*. That is the right instinct — assert the
shape rather than assume it — and matches the ZD-2 rule that a 200 which looks wrong is an
error, not something to hand downstream.

## 5. Error handling — the vendor confirms our findings and is coarser than we need

`ZendeskAPI::Error::RecordInvalid`:

```ruby
@errors = response[:body]["details"] || generate_error_msg(response[:body])
# ...
response_body.values_at("description", "message", "error", "errors").compact.join(" - ")
```

Two independent confirmations in five lines. It reaches for **`details` first** — the array
carrying a field id and type per violation, which our 422 experiment established is the entire
actionable content of a validation refusal. And when that is absent it joins **four different
keys**, because Zendesk's own client cannot predict which envelope shape it will receive
either.

Their taxonomy is five classes — `ClientError`, `RecordInvalid`, `RecordNotFound`,
`RateLimited`, `NetworkError` — mapped as:

| Status | Raises |
|---|---|
| 404 | `RecordNotFound` |
| 422, 413 | `RecordInvalid` — note **413 Payload Too Large** treated as validation |
| everything else 4xx/5xx, plus odd 1xx/3xx | `NetworkError` |

**That last row is too coarse for us.** A `403` becomes a "network error", which is precisely
the plan-boundary-versus-outage confusion our access audit exists to prevent, and a `404`
gives `RecordNotFound` whether the body says `RecordNotFound` or `InvalidEndpoint` — losing
the "your plan" versus "your id" distinction. Our taxonomy should be finer, and now has a
stated reason to be.

**New to us: `503` is retryable.** Their retry middleware treats `429` **and `503`** as
retryable, `503` being maintenance. We had accounted for 429 only.

Their retry is otherwise minimal — **one attempt**, honouring `Retry-After` with a 10-second
default, no backoff. Worth exceeding, but the `Retry-After` default is a useful number to
adopt for responses that omit the header.

## 6. Coverage, for calibration

| | Resource classes | Help Center |
|---|---:|---|
| Ruby (official) | 110 | 1 file, minimal |
| PHP (official) | 78 | 4 resources |

Both are Support-centric. **Neither official client meaningfully covers Help Center** — which
is half of our 1.0 scope, and the same gap the OpenAPI spec has. That consistency is itself a
finding: Help Center is under-served by Zendesk's own tooling, not just by the community's.

## 7. What we take

| From | Take |
|---|---|
| CBP guide | The sortable-attribute divergence, and translating "newest" to `sort=-id` |
| PHP `unsetObpParams` | Never emit both pagination styles; refuse rather than trust |
| Ruby `cbp_path_regexes` | Seed our cursor-capability table; confirms search has none |
| Ruby `cbp_response?` | Detect pagination style from `meta` + `links` at runtime |
| PHP `PaginationError` | Assert the envelope shape; raise when it is not what was expected |
| Ruby `RecordInvalid` | Read `details` first, then fall back across all four envelope keys |
| Ruby retry | Treat `503` as retryable alongside `429`; 10s default when `Retry-After` is absent |

And what we deliberately do **not** take: their status-to-error mapping, which collapses
`403` and every other 4xx into a network error.

---

## 8. Do the two official clients agree? No — they diverge substantially

Compared by resource surface, normalising Ruby's singular CamelCase against PHP's plural file
names, and excluding base classes, middleware and transport.

| | |
|---|---:|
| Ruby resources | 65 |
| PHP resources | 65 |
| **present in both** | **33** |
| union | 97 |
| overlap of the union | **34%** |
| of PHP's surface, present in Ruby | 51% |
| of Ruby's surface, present in PHP | 51% |

Identical size, half the content. Each official client reaches about **half** of what the other
does.

### Some of that is modelling, not capability

A resource absent as a *class* may still be reachable as a *method*. Checked by grep:

| Capability | Missing as a class from | Actually reachable there? |
|---|---|---|
| incremental export | PHP has it, Ruby lacks the class | **yes** — methods on resources |
| ticket comments | PHP class, no Ruby class | **yes** — via `Comment` / `Event` |
| locales | PHP class, no Ruby class | **yes** |
| agents | Ruby class, no PHP class | **yes** — methods |
| user identities | Ruby class, no PHP class | **yes** |
| settings | Ruby class, no PHP class | **yes** |

### But several gaps are real

Nothing found in the whole library, not as a class and not as a method:

| Capability | Ruby | PHP |
|---|---|---|
| **Job statuses** | **absent** | present |
| SLA policies | **absent** | present |
| Help Center translations | **absent** | present |
| User / organization custom fields | **absent** | present |
| Custom roles | **absent** | present |
| Talk greetings | present | **absent** |
| Deleted tickets | present | **absent** |
| Community topics | present | **absent** (one incidental hit) |
| Schedules | present | **absent** (one incidental hit) |

*Caveat: cross-language name normalisation is approximate, and a grep returning nothing is
strong evidence rather than proof. The direction and rough magnitude are reliable; individual
rows may be off.*

### Three things worth taking from the comparison

**1. Neither client is authoritative on its own.** Where they were used above as evidence — the
cursor-pagination path list, the error-envelope handling — that evidence is stronger where both
agree and weaker where only one speaks. Their *union* is a better map of the API than either,
and even the union is a **floor, not a ceiling**: our inventory has 125 families against their
combined 97 resources.

**2. PHP is the only client in anything we have surveyed that models job statuses.** Not the
Ruby client, and none of the twelve MCP servers. But it models it as a bare `Find`/`FindMany`
resource — **no polling, no completion semantics, no partial-failure handling.** So the async
bulk lifecycle is genuinely unsolved everywhere, and remains one of the four real gaps.

**3. Even PHP's incremental export uses the older interface.** Its routes are
`incremental/tickets.json`, `ticket_events`, `organizations`, `users` — the **time-based** form,
not the `incremental/tickets/cursor.json` variant Zendesk now documents as preferred and which
our probing confirmed works. So on the one bulk primitive an official client does model, it
models the superseded version.

### What this means for us

The divergence removes a temptation. There is no canonical client surface to mirror, so the
question "what does a good Zendesk client expose?" has no established answer to defer to — the
two official ones disagree with each other about half of it. That makes our own inventory the
better organising principle, with the clients as corroboration on specific behaviours rather
than as a template for scope.
