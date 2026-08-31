# Does the API enforce required-on-solve, and does it say what is missing?

**Run 2026-08-31 against ticket the test ticket** (a disposable test ticket), a form with no conditional rules,
status `new`, its other required fields already set.

The agent UI refuses to submit this ticket as solved and shows, in red:

> "<numeric field>" needed
> "<choice field>" needed

The question was whether the API enforces the same rule, and whether it says *which* fields —
because the answer decides how `update_ticket` is shaped.

## Result: yes, and the API is better than the UI

```
PUT /api/v2/tickets/the test ticket.json   {"ticket": {"status": "solved"}}
→ HTTP 422
```

```json
{"error":"RecordInvalid","description":"Record validation errors","details":{"base":[
 {"description":"Assignee: is required when solving a ticket",
  "error":null,"ticket_field_id":<id-1>,"ticket_field_type":"FieldAssignee"},
 {"description":"<numeric field>: is required when solving a ticket",
  "error":null,"ticket_field_id":<id-2>,"ticket_field_type":"FieldInteger"},
 {"description":"<choice field>: is required when solving a ticket",
  "error":null,"ticket_field_id":<id-3>,"ticket_field_type":"FieldTagger"}]}}
```

Four things follow.

1. **Enforcement is server-side, not UI-only.** The earlier worry — that a tool could set
   `solved` and leave a record the UI considers invalid — does not apply. The constraint is
   real and the API is the one applying it.
2. **The error is structured, not prose.** `details.base[]` gives a `description`, a
   `ticket_field_id` and a `ticket_field_type` per violation. That is machine-actionable: a
   tool can enumerate exactly what is missing without parsing English.
3. **The API reports MORE than the UI.** Three fields, where the red banner showed two —
   `Assignee` is also required and the banner omitted it. Anything built on the UI's message
   would under-report.
4. **The ticket was not modified.** Status stayed `new`. The write is rejected whole.

## What the error still does not carry: allowed values

The error names the field and its type but not its choices. Using only the ids it returned:

| Field | Type | Choices in the error | Choices on lookup |
|---|---|---|---|
| Assignee | `assignee` | — | n/a (user/group id) |
| <numeric field>| `integer` | — | n/a (free integer) |
| <choice field> | `tagger` | — | **2** |

So for free-text and numeric fields the 422 is sufficient to act on. For a **tagger** the caller
learns *that* a choice is required but not *which* choices exist, and needs one more call —
`GET /api/v2/ticket_fields/{id}.json` — which is exact and cheap because the error handed over
the id.

**Cost of the reactive path:** 1 failed write + 1 lookup per choice-field + 1 successful write.

## Secondary findings

- **A private note is a plain update.** `PUT /tickets/{id}` with
  `comment: {body, public: false}` → 200, comment created with `public: false`, and **status is
  unchanged**. Adding a note and changing state are the same endpoint, which is what the UI's
  single submit implies.
- **A fourth error envelope.** `API-SURFACE.md` §5.5 catalogues three incompatible error shapes.
  This is a variant of the second (`{"error": str, "description": str}`) carrying an extra
  `details` object — and `details.base[]` is where everything useful lives. An error parser that
  reads only `error` and `description` gets "RecordInvalid / Record validation errors" and
  throws away the entire diagnosis.

## Bearing on the design

This materially strengthens the case for a **single `update_ticket` with a rich error path**,
and weakens the case for composite `solve_ticket`-style tools: the platform already computes
required-ness correctly, per form, at the moment of the call, and reports it in a parseable
shape. Nothing we could hardcode would be as accurate, and anything we hardcode goes stale when
an admin edits a form.

A proactive `transitions` block on the read is now an **optimisation rather than a
correctness measure** — it saves a failed call and a lookup, at the cost of extra reads. That
trade is a judgement call, no longer a safety one.

What the tool layer must do regardless:

- Surface `details.base[]` in full. Discarding it turns an actionable refusal into "HTTP 422".
- Resolve choice-field ids to their allowed values before handing the problem back, so the
  caller gets *"<choice field> must be two allowed values or two allowed values"* rather than *"<choice field> is
  required"*.
- Never infer a value to satisfy the constraint. <choice field> picks which legal entity the
  work is booked against; Department feeds QMS reporting. A tool that guesses to get past a
  validation gate is fabricating audit evidence.

## Reproduce

```bash
set -a; . ./.env; set +a
python3 experiments/solve-required-fields/01_read.py            # state, redacted
python3 experiments/solve-required-fields/02_private_comment.py # adds an internal note
python3 experiments/solve-required-fields/03_attempt_solve.py   # the 422
python3 experiments/solve-required-fields/04_resolve_choices.py # ids -> allowed values
```

Steps 2 and 3 write to a real ticket. Step 3 is rejected, so the only lasting effect of the
whole run is one internal note.

---

# Addendum, same day: the requirements are queryable *before* the call

Follow-up question: can we ask Zendesk what a ticket needs in order to be solved, rather than
finding out by being refused?

**Yes.** Requirements are published as data, in two places, and combining them reproduces the
422 exactly.

## The two mechanisms

**1. Global — `ticket_fields[].required`**

Documented as "required when solving a ticket". Account-wide, but it only bites for fields that
are *on the ticket's form*: many fields carry the flag, yet ticket the test ticket was blocked by three, because its form carries only a subset of them.

**2. Conditional — `ticket_forms[].agent_conditions[]`**

A rule set of the shape *"IF parent field = value THEN these children are required, on these
statuses"*. From the one multi-stage form:

```json
{"parent_field_id": <parent-id>,          // "<parent choice field>"
 "value": "yes",
 "child_fields": [
   {"id": <child-id>, "is_required": true,
    "required_on_statuses": {"type": "SOME_STATUSES", "statuses": ["solved"]}},
   ... ]}
```

…and the `"no"` branch instead requires *a justification field*. So that form encodes a
real workflow: **either** do the corrective action and record its description, due date and
evidence, **or** justify not doing it. That is process, expressed as configuration.

## Verification

`05_compute_requirements.py` computes blockers from those two sources and compares them with
what the API actually refused:

```
COMPUTED : Assignee (assignee) · <numeric field>(integer)
           <choice field> (tagger)  choices: two values
API 422  : Assignee · <numeric field>· <choice field>
MATCH
```

**The computed answer is strictly better than the error**, because it carries the allowed values
(two values) that the 422 omits — no second lookup needed.

## The catch: the structure is undocumented

Zendesk's ticket-forms reference names `agent_conditions` as "Array of condition sets for agent
workspaces" and stops. `parent_field_id`, `child_fields`, `is_required` and
`required_on_statuses` are not specified anywhere, and `required_on_statuses.type` is not
enumerated — `SOME_STATUSES` is observed, `ALL_STATUSES` is inferred and **unverified**.

So this is reverse-engineered structure validated against the enforcer. The mitigation is to keep
that comparison as a **conformance test** rather than a one-off: compute the blockers, attempt
the write, and assert the two agree. If upstream changes the shape, that fails loudly instead of
the precheck quietly under-reporting — which is the dangerous direction, since a precheck that
misses a requirement reports "ready to solve" and is then refused.

## Also found

- **`/api/v2/ticket_form_statuses` → 403.** Docs say admins and agents may list it. We are an
  admin, so this is feature availability rather than role: this account has defined no custom statuses (`default: true`), so no per-form status mapping exists to
  read. It would matter for an account that had defined extra statuses.
- **60 GET endpoints across the API describe configuration rather than data** — fields, forms,
  locales, brands, custom roles, SLA and routing definitions, macro/trigger/view definitions,
  account settings. Enough to describe an account's setup without touching a single ticket, which
  is a capability worth exposing on its own.

---

# Addendum, 2026-08-31: closing the test ticket, and what it cost

Two verification gaps were probed together. The first destroyed the means of answering the
second.

## What was run

```
PUT /api/v2/tickets/{id}  {"ticket": {"status": "closed"}}    -> 200
PUT /api/v2/tickets/{id}  {"ticket": {"comment": {...}}}      -> 422
   {"details": {"status": [{"description": "closed prevents ticket update"}]}}
```

The first call was framed as *"confirm `closed` is rejected"*. It was not rejected. The ticket
closed, and closed is terminal — reopening returns the same 422, as does any other write. The
second call, which was to establish whether `comment.public` defaults to public or private, could
not run, and that question is now blocked on provisioning another disposable ticket.

## Findings, which are real and worth having

1. **`status: "closed"` is accepted through the API.** An earlier assertion in
   `API-SURFACE.md` — that Closed was automation-only and unreachable by an agent — was wrong.
2. **Closed is terminal.** Every subsequent write is refused. There is no reopen; the platform's
   answer is a follow-up ticket. Reads still work.
3. **The web UI does not offer Closed** in its status picker. This is an action the API permits
   and the interface declines to expose — the opposite of the usual direction.
4. **`details` is a map from field name to problems**, not a fixed shape. Required-fields
   refusals use `details.base[]`; this one uses `details.status[]`. Iterate the keys; never
   index `base`.

## What went wrong, and the rule that follows

`SECURITY.md` in this repository contains a table ranking ticket operations by reversibility. It
was written hours before this probe. Closing was not on it — because the table was built from the
belief that closing was not an available action, which is exactly the belief the probe disproved.

The error was not running a write. Writes on that ticket were authorised. The error was treating
**"I expect this to be rejected" as a safety property.** A probe whose stated purpose is to
confirm a refusal is, by construction, a probe that does something unknown if the refusal does
not happen — and the less certain the expectation, the more likely the operation is one nobody
has characterised.

**Rule: before any write probe, state what happens if it SUCCEEDS, and whether that is
reversible. If the answer is unknown, it is not a cheap probe.** The cost here was a designated
test ticket and a blocked verification; on a real ticket it would have been a customer record
frozen with no way back.

Corollary for the design, which is the useful part: **`close` is the most destructive ordinary
operation on a ticket** — worse than solve, which reverses, and worse than a field edit, which is
audited. It needs its own capability gate, separate from writes and separate from solve, and a
tool description that says plainly that it cannot be undone.
