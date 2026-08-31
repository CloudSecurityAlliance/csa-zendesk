# Mapping tools to what the web interface actually does

An 882-operation API is the vendor's implementation shape. It is a poor organising principle
for a tool surface, because nobody thinks in `PATCH /api/v2/tickets/{id}` — they think *"assign
this to Ops and mark it pending."*

So the design is organised around **the actions the Zendesk agent interface offers**, and this
document establishes what those are — not by reading the UI, but by asking Zendesk.

---

## 1. Zendesk publishes its own UI vocabulary

Macros, triggers, automations and views are how the interface packages what a person can *do* to
a ticket and what they can *filter* on. Zendesk exposes those definitions through the API,
**computed against this account**:

| Endpoint | What it enumerates | Count here |
|---|---|---:|
| `/api/v2/macros/definitions.json` | Everything a macro can set on a ticket | **30 actions** |
| `/api/v2/triggers/definitions.json` | Everything automation can do, and match on | **56 actions**, 95 conditions |
| `/api/v2/views/definitions.json` | Everything a queue can filter, show, group, sort by | 72 conditions, **79 output columns** |

Regenerate with `scripts/ui_actions.py` → `analysis/ui-action-vocabulary.json`.

Because these are account-specific they enumerate CSA's real configuration — its custom fields, forms, statuses and groups — not a generic reading of the docs. **This is the
bridge between the API inventory and a tool surface a person recognises.**

> **PII note.** 23 choice lists are withheld from the artifact: assignees, followers, groups, and
> ~80 internal routing addresses under `Received at`. Counts are kept, values are not. The first
> version of the scrubber gated on *field name* and leaked the addresses, because `Received at`
> was a subject nobody thought to list; it now gates on the **value**, so an unanticipated
> subject cannot leak.

## 2. The vocabulary is tenant-specific, and that is the point

The definitions endpoints enumerate against **this account's** configuration, so what comes
back is not a generic Zendesk vocabulary — it is the set of actions and fields *this*
organisation has configured. Ours turns out to be built around quality-management and
incident-response workflows rather than around customer support: multi-stage processes with
conditional required fields, due dates, verification steps and named accountable parties.

The specifics are CSA operational detail and live in the private tenant extract, not here.
What matters for the design is the shape:

- The custom-field vocabulary encodes **process**, not just data. Fields chain — one field's
  value determines which others become mandatory.
- Several forms coexist with **materially different** requirements, from three required fields
  to twenty.
- The workload those forms imply is **read, filter, correlate and report** — "which items have
  a verification date that has passed", "which open items have no accountable party" — rather
  than the conversational answer-the-customer loop the third-party servers optimise for.

That last point is the one that should shape the tool surface: ticket metrics, satisfaction,
views and incremental export matter more here than `create_ticket` does.

## 3. The mapping

Grouped by the UI surface a user would name, with the API operations behind it and the tool
each becomes. **Proposed** — this is the input to the architecture decision, not the decision.

### The ticket, as an agent sees it

| UI action | API | Tool |
|---|---|---|
| Open a ticket; read the conversation | `GET /tickets/{id}`, `/tickets/{id}/comments` | `get_ticket`, `get_ticket_comments` ← consensus names |
| Reply publicly / add internal note | `PUT /tickets/{id}` with `comment.public` | `create_ticket_comment` ← consensus name |
| Set status, priority, type, form, brand | `PUT /tickets/{id}` | `update_ticket` ← consensus name |
| Assign to agent or group | `PUT /tickets/{id}` | folded into `update_ticket` |
| Add / remove / set tags | `PUT /tickets/{id}`, `/tags` | folded into `update_ticket` |
| Set a custom field (QMS, IR, RCA, Dept) | `PUT /tickets/{id}` `custom_fields[]` | folded into `update_ticket`, with `list_ticket_fields` to discover ids |
| Add follower / CC | `PUT /tickets/{id}` | folded into `update_ticket` |
| Side conversation (email or child ticket) | `/tickets/{id}/side_conversations` | `create_side_conversation` — **nobody in the field has this** |
| Merge tickets · mark as spam | `/tickets/{id}/merge`, `/mark_as_spam` | `merge_tickets`, `mark_ticket_as_spam` |
| Attachments | `/attachments/{id}`, uploads | `get_ticket_attachment` ← consensus name |
| Audit trail — who changed what | `/tickets/{id}/audits` | `get_ticket_audits` |

### Queues — the views sidebar

| UI action | API | Tool |
|---|---|---|
| List my views | `GET /views` | `list_views` ← consensus name |
| Open a view; see its tickets | `/views/{id}/tickets`, `/execute` | `get_view_tickets` |
| Ticket count badge | `/views/{id}/count` | folded in |
| Build a filter (72 conditions, 79 columns) | `/views` POST/PUT | `create_view`, `update_view` |

### Search — the top bar

| UI action | API | Tool |
|---|---|---|
| Search anything | `GET /search.json` | `search` ← consensus name |
| Export a large result set | `GET /search/export.json` | `export_search` — **the one that actually pages**; see `API-SURFACE.md` §5.2 |

### Macros, triggers, automations — the admin surface

| UI action | API | Tool |
|---|---|---|
| List / preview / apply a macro | `/macros`, `/macros/{id}/apply` | `list_macros`, `apply_macro` |
| What can a macro set? | `/macros/definitions` | `describe_ticket_actions` — the 30-item vocabulary above |
| Triggers, automations, SLA policies | `/triggers`, `/automations`, `/slas/policies` | `list_*` / `get_*` per family |

### People

| UI action | API | Tool |
|---|---|---|
| Look up a user / organization | `/users/{id}`, `/organizations/{id}` | `get_user`, `get_organization` ← consensus names |
| Search users | `/users/search` | `search_users` |
| Group membership | `/group_memberships` | `list_group_memberships` |

### Reporting — what CSA actually does

| UI action | API | Tool |
|---|---|---|
| Ticket metrics — response, resolution | `/ticket_metrics` | `get_ticket_metrics` — two servers have it |
| Satisfaction ratings | `/satisfaction_ratings` | `list_satisfaction_ratings` — one server has it |
| Bulk export since a timestamp | `/incremental/tickets/cursor` | `export_tickets_since` — **nobody in the field has this**; 10 req/min bucket |
| Async job result | `/job_statuses/{id}` | `get_job_status` — **nobody has this** |

### Help Center

| UI action | API | Tool |
|---|---|---|
| Find / read / write an article | `/help_center/articles*` | `search_articles`, `get_article`, `create_article`, `update_article` |
| Sections, categories, labels | `/help_center/{sections,categories,...}` | `list_*` per family |
| Translations | `/articles/{id}/translations` | `list_translations`, `update_translation` |

## 4. What this changes

1. **It gives the tool surface a principle.** Not "wrap 882 operations" and not "copy the field's
   47" — expose *what a person can do in the interface*, which is a bounded, nameable set that
   maps onto how users ask for things.
2. **It makes the gap concrete.** Three tools above are marked *nobody in the field has this* —
   incremental export, job status, side conversations. Metrics and satisfaction were also on
   that list until a source-level re-read found two servers implementing them; the correction
   is recorded in `PRIOR-ART.md` §7. Those three remain the bulk and async primitives an
   analytical workload needs and no existing server provides.
3. **It shapes the capability policy.** UI actions group naturally into capabilities —
   `ticket.read`, `ticket.comment`, `ticket.admin`, `hc.write`, `people.write`, `reporting.read`
   — which is a far better fit for a `PolicyBackend` gate table than 882 endpoint names, and maps
   cleanly onto Zendesk's 54 OAuth scopes.
4. **It leaves `update_ticket` doing a lot.** Assign, status, priority, tags, followers and 130
   custom fields are all one `PUT`. Whether that stays one tool or splits into several is a real
   open question — one tool matches both the API and the field's naming; several would match how
   people describe the actions. To settle in the architecture decision.
