# F1 — the live walkthrough, 2026-09-22

First time any of Block 1's write surface touched live Zendesk. Run against the disposable test
ticket on the CSA tenant, with `CSA_ZD_ALLOWLIST_READ` and `CSA_ZD_ALLOWLIST_WRITE` both set to that
single id, through the library at the `ZendeskClient`/`PolicyBackend` seam (the same path
`server.call_tool_sync` takes) at `E2_CAPABILITIES`.

Ticket id and content are withheld here; `before.json` holds the pre-run state and is gitignored.

## What ran, and what the API actually did

| Step | Result | What it taught |
|---|---|---|
| `get_ticket` | ok | Allowlist admits the listed id; refuses an unlisted one client-side (verified separately). |
| `update_ticket` `{priority: "low"}` | ok | The allowlist inverted in Block 1 permits it, and Zendesk accepted it. |
| `assign_ticket` `assignee_id=<self>` | ok | **Side effect: `status` moved `new` → `open` on its own.** We did not ask for that and the tool does not mention it. |
| `upload_file` | ok | Returned a token. The file is attached to nothing at this point, as designed. |
| `add_internal_note` `uploads=[token]` | ok | **`public: false` confirmed on the live audit event** — the load-bearing literal, verified against the real API for the first time rather than against `FakeBackend`. Attachment landed, 91 bytes, correct filename. |
| `get_attachment` | ok | Returned the attachment envelope. |
| `solve_ticket` | **refused by Zendesk** | `ValidationError` — see below. |

## Finding 1 — `solve_ticket` cannot solve a ticket on this tenant, and the API says why

```
ValidationError: Zendesk refused the record - base: <field A>: is required when
solving a ticket, <field B>: is required when solving a ticket
```

(Two tenant-specific required custom fields. Names withheld — they are internal field names and
this file is in a public repo; the publication gate refuses them, correctly. The shape of the
message is the finding, not the field names.)

This settles the question `experiments/solve-required-fields/` was opened to answer: **the API
enforces the same required-field constraint as the agent UI, and it names the fields.** It does not
fail silently or generically.

Our error handling surfaces it well — the model gets a `ValidationError` whose message names both
missing fields, which is actionable rather than a bare 422.

**Consequence for the tool surface:** `solve_ticket` alone cannot complete a ticket on this tenant.
The workflow is `update_ticket(custom_fields=…)` then `solve_ticket`, and `custom_fields` *is* on
`_TICKET_EDITABLE_FIELDS`, so the path exists at E2 — but it needs the field ids and their valid
choices, which requires reading `ticket_fields`, an admin read this rung does not have. **So E2 can
work a ticket but cannot finish one here.** That is a real gap in the rung, not a bug in the tool.

## Finding 2 — `update_ticket` is not as reversible as our own boundary table claims

```
ValidationError: Zendesk refused the record - priority: Priority: cannot be reset
```

Once `priority` is set it cannot be cleared back to unset. `analysis/tool-boundaries.csv` records
`update_ticket` as `reversible`; that is **true for changing a value and false for clearing one**,
and the table did not distinguish them. Corrected.

This is exactly the class of thing only a live run finds: every offline test set a value to another
value, and the fake accepted anything.

## Finding 3 — the E2 surface can assign but cannot unassign

Two independent refusals, both correct in isolation, combining into a one-way door:

- `update_ticket(fields={"assignee_id": None})` → refused by **our own allowlist**. `assignee_id` is
  deliberately absent from `_TICKET_EDITABLE_FIELDS` because assignment is `assign_ticket`'s job
  (ADR-016 — a tool is an operation *and* its constrained arguments).
- `assign_ticket(assignee_id=None, group_id=None)` → refused as an **`EmptyWrite`**, because a call
  naming neither field would be a no-op that still spends write budget and still lands in the audit
  log.

Neither refusal is wrong. Together they mean an agent that assigns a ticket cannot undo it, and
nothing in the tool descriptions says so. Worth a decision rather than a silent gap: either
`assign_ticket` grows an explicit unassign affordance, or the one-way-ness is documented.

`status: new → open` is likewise irreversible — `new` means "never touched", so nothing can put it
back. That is Zendesk's model, not ours, but it belongs in the same note.

## Finding 4 — G3 resolved: attachments are NOT anonymously fetchable on this tenant

```
GET <attachment content_url>   (no Authorization header, no cookie)
→ HTTP 403 Forbidden
```

The tenant has *"require authentication to download attachments"* enabled. So the larger version of
`TODO.md` G3 — *the server emits a URL anything with sight of the model's context can fetch, outside
the credential entirely* — **does not hold here.**

G3 therefore stays a **documentation** item rather than becoming a blocker: `get_attachment` is
still unscoped by any allowlist, so an install pinned to one ticket can read any attachment **the
credential can reach** — a blast-radius gap, not a credential-bypass one.

**This is a tenant setting and can be turned off.** The check is worth re-running if the tenant
config changes; it is not a property of the code.

## What was left changed

The run is deliberately not fully reverted, because two of its changes cannot be:

| Field | Before | After | Why not restored |
|---|---|---|---|
| `status` | `new` | `open` | `new` is unreachable once a ticket is touched |
| `priority` | unset | `low` | Zendesk refuses to reset priority (Finding 2) |
| `assignee_id` | unset | the test account | The E2 surface cannot unassign (Finding 3) |
| comments | 1 | 2 | An internal note with one attachment; not deleted |

All on the disposable test ticket, all within the allowlist.

## What F1 did not cover

- **`reply_publicly` / `merge_tickets`** — rung E5, not built (`TODO.md` G2).
- **`close_ticket`** — deliberately not built; closed is terminal (`TODO.md` G1).
- **Whether Zendesk's plain `body` strips `display:none` text from inbound email HTML.** Still open.
  It needs an email *sent* to the support address carrying hidden HTML, then read back through
  `list_comments` — a different experiment from this one, and it creates a real ticket in the queue.
  This is the question that decides how much [DEC-018](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/DECISIONS.md)'s
  conversion buys us on the Zendesk read path specifically.
