# F2 — the email-originated ticket, 2026-09-24

The first time Block 2's HTML→Markdown conversion met real Zendesk HTML. Block 2 shipped on
2026-09-22 having been tested only against fixtures written by its own author, and F1 ran
*before* it merged — so until now the conversion that rewrites every ticket body a model sees
had never once run against live data.

**Method.** An HTML-only email (no `text/plain` part, deliberately — see Finding 1) was sent to
the support address from an internal account, creating an ordinary email-originated ticket. It
carried the shapes real support mail actually uses — headings, a list, an anchor, bold/italic, a
table, a quoted reply, an `<hr>` signature — plus three sentinel strings: text hidden by
`display:none`, text hidden by `font-size:0`, and a scoped `U+202E` bidi override. The ticket was
then read back through `search_tickets` and `list_comments`.

Sentinels were used so no finding rests on reading prose carefully.

**Verdict: the conversion works.** Every real shape survived, both hidden sentinels were caught,
and the override was stripped. Four findings came out of it anyway, and two are worth acting on.

---

## What the conversion did, on real HTML

| Shape | Result |
|---|---|
| headings | `### Why this fixture exists` |
| unordered list | `* item` |
| anchor | `[link](https://…)` — href preserved |
| bold / italic | `**bold**` / `*italic*` |
| **table** | full Markdown table, including the `\| --- \|` separator row |
| blockquote | `>` prefix, correctly nested on each line |
| `<hr>` | `---` |
| signature line breaks | two-space hard breaks, so the block stays one paragraph |

No shape was lost or mangled. The table is the one worth calling out: it is the shape most likely
to degrade into unreadable prose, and it came through structurally intact.

`hidden_text` carried both sentinels and both were **removed** from the Markdown, which is the
contract Block 2 set — surfaced beside, not inline, and not silently dropped.

---

## Finding 1 — Zendesk's plain-text renderings keep hidden text as ordinary prose. Confirmed live.

The project has asserted since DEC-018 that reading `body` instead of `html_body` is *"strictly
worse than converting the HTML ourselves"*, because Zendesk's plain-text rendering is a naive tag
strip: it discards the CSS that marked text as hidden while keeping the text. That was reasoning
from the API's shape. It is now observed.

The same comment, three ways:

| Field | `display:none` sentinel | `font-size:0` sentinel |
|---|---|---|
| `body` | **present, inline, indistinguishable from real prose** | **present, inline** |
| `plain_body` | **present, inline** | **present, inline** |
| `html_body` (converted) | removed → `hidden_text[0]` | removed → `hidden_text[1]` |

Concealed text arrives in the plain-text fields looking exactly like something the sender wrote
and meant. The evidence that it was hidden — the CSS — is the part Zendesk throws away.

This is the answer to the question F1 left open, and it validates the decision to read `html_body`
and convert rather than take the vendor's plain text.

## Finding 2 — but the undefended renderings are still in the envelope, and nothing says so

Block 2 defends `html_body`. `body` and `plain_body` are returned **untouched** alongside it, and
they carry exactly the payload the conversion exists to neutralise:

- both hidden sentinels, inline, as ordinary prose
- the `U+202E` override, unstripped — `strip_suspicious` never runs on these fields

So a single `list_comments` response contains one defanged rendering of the comment and two
undefended ones, and nothing in the payload marks which is which. A model summarising a ticket
picks whichever field it likes. On this fixture, two of the three choices are wrong.

**This is the finding worth acting on.** Options, roughly in increasing cost:

1. Convert/strip `body` and `plain_body` too — cheap, but they are *supposed* to be the vendor's
   plain rendering, and rewriting them makes the envelope lie about what Zendesk returned.
2. Drop them from the envelope entirely, since `html_body` now supersedes both — smallest surface,
   but discards data some caller may want.
3. Leave them and say so in the tool description, so the model is told which field is the safe one.

Worth noting the failure is *silent* under all three today: nothing currently warns a reader that
`body` may contain concealed text. Whatever is chosen, that sentence is the minimum.

## Finding 3 — WITHDRAWN: the missing `(neutralised)` label is deliberate

*(Corrected 2026-09-24, the same day it was filed. The measurement below stands; the conclusion
drawn from it was wrong. Left in place rather than deleted, because the mistake is the instructive
part.)*

`_MARKUP_KEYS = frozenset({"html_body"})`, and `_walk_dict` passes `note_on_change=False` for those
keys. Two tests pin it. The reasoning sits in the source beside the definition, and it is the same
reasoning that rejected a colour rule for hidden text:

> `html_body` is Markdown; Markdown uses `>` for blockquotes; a quoted reply appears in most support
> tickets. So the note would fire on nearly **every** comment — noise exactly where a real injection
> attempt would arrive, since a genuine escape attempt reads identically to routine Markdown.

**A signal that fires constantly is not a signal.**

The lesson is not that the measurement was sloppy — it was reproduced offline with identical input
to both fields, and it is correct. It is that *"the code does not do X"* and *"the code should do
X"* are different claims, and only the first one was measured. The second was assumed, in a file
whose neighbouring comment answers it in full — a comment this same session had already read while
closing a different residual.

What follows is the original finding, unedited.

### Original finding — `html_body` is neutralised without being labelled neutralised

The provenance marker appends `(neutralised)` when markup characters in a field were altered. On
the live response, `body` was labelled; `html_body` was not — **though both had been altered.**

Reproduced offline with identical input to both fields, to remove any doubt it was a property of
the differing content:

```
markdownify emits: '> quoted reply'

html_body:  marker says neutralised: False    content: '› quoted reply'
body:       marker says neutralised: True     content: '› quoted reply'
```

Same input, same alteration, different label. The under-reporting lands on **the field Block 2
made primary** — a reader diffing the Markdown against the source finds characters changed with
nothing in the envelope accounting for it. The marker is the provenance contract, and here it
under-claims.

## Finding 4 — the override is stripped, its terminator is not

`U+202E` (RLO) was stripped from `html_body` as designed. The matching `U+202C` (PDF) that scoped
it survived, because `_STRIP` deliberately holds only the LRO/RLO override pair — `U+202C`
terminates ordinary right-to-left embeddings too, so stripping it would damage legitimate text
(H4).

Correct by design, and harmless: a terminator with nothing to terminate has no visual effect. Noted
because the asymmetry looks like a bug until you know why, and someone will eventually ask.

---

## Two observations, not findings

**Requester geolocation rides along.** Every email comment carries
`metadata.system.location` plus latitude and longitude, resolved from the sender. It is Zendesk's
data and the tool is right to pass it through untouched — but it is personal data arriving in model
context on every read, and no caller asked for it. Worth a deliberate decision rather than a
default.

**Envelope bloat.** The ticket returned 54 `custom_fields` entries, every one `null`, and the same
list again under `fields`. On a tenant with a large form set this is the dominant cost of a read,
and it is pure padding.

---

## The write half, same day

`CSA_ZD_ALLOWLIST_WRITE` was set to this ticket alone and the server restarted. First check: a write
to an id *outside* the allowlist, which refused before any API call and named the variable to
change. The allowlist was loaded and scoping.

### Finding 5 — `add_internal_note` forces `public: false` on a ticket that defaults to public. Confirmed on the live audit.

This is the test the fixture was chosen for. On an email-originated ticket the first comment is
public, and `comment.public` **inherits from it** — so on this ticket, a comment that does not
force otherwise is emailed to the requester. The hardcoded `public: false` in
`add_internal_note` is the only thing standing in the way.

Zendesk's own audit event for the note:

```
"type": "Comment",  "public": false
```

Read from the audit event Zendesk returned, not from our own request and not from `FakeBackend` —
which is the point. Until now that literal was verified only against a double that was told what to
say. **The control holds.**

Worth stating plainly: a fixture whose comments default to *private* could not have tested this. It
would have passed whether the control existed or not.

### Finding 6 — `assign_ticket` changes three fields, and only one was asked for

F1 recorded that assignment silently moves `status` `new`→`open`. The audit for a single
`assign_ticket(assignee_id=…)` call shows **three** changes:

| field | previous | new | asked for? |
|---|---|---|---|
| `assignee_id` | null | the agent | **yes** |
| `group_id` | one group | **a different group** | **no** |
| `status` | `new` | `open` | **no** |

The `group_id` change is new — F1 never saw it, and nothing in the tool description mentions it.
Assigning to an agent moved the ticket into that agent's group, which is Zendesk behaviour, not
ours. But it means a tool presented as *"set the assignee"* reassigns queue ownership as a side
effect, and on a tenant that routes by group that is a visible, potentially disruptive change
nobody requested.

This matters more here than it would elsewhere. ADR-016 splits tools so each is
`(operation × constrained arguments)` — `assign_ticket` exists as its own tool *precisely* so its
blast radius is legible. A tool with two undocumented side effects is not legible, and the split
bought less than it appeared to.

Both side effects belong in the tool description. G4 already asks for the `status` one; `group_id`
is added to it here.

### Finding 7 — G4's one-way door, confirmed live

Both halves refuse, both with accurate messages:

- `update_ticket(fields={"assignee_id": None})` → refused by the field allowlist, which names the
  nine editable fields and says assignment has its own tool.
- `assign_ticket()` with neither argument → refused as an empty write, on the grounds that it would
  spend the tenant's write-rate budget and land in the audit log having changed nothing.

Each refusal is right on its own terms. Together they mean **assignment at rung E2 cannot be
undone by this tool**, which is what G4 records. Confirmed rather than reasoned, now.

### Finding 8 — `solve_ticket` fails identically on an email-originated ticket

Zendesk refused it, naming **two required custom fields** that must be set before any ticket on
this tenant can be solved. (The field names are tenant configuration and stay out of this
repository; they are in the operator's own notes.)

Byte-for-byte the refusal F1 got on a different fixture. So the requirement is **tenant form
configuration, not a property of the ticket type** — which narrows it usefully: `solve_ticket`
cannot solve *any* ticket on this tenant as built.

There is a path, and it is worth recording because it is not obvious: both required fields are
custom fields, and `update_ticket`'s allowlist *does* include `custom_fields`. So solving is
`update_ticket(custom_fields=…)` followed by `solve_ticket` — two tools, in order. Nothing in
either tool's description says so, and a model asked to "solve this ticket" has no way to discover
it except by failing first.

### Observation — the operator's IP and location ride on every write

Every write audit carries `metadata.system.ip_address`, `location`, latitude and longitude. On
reads (Finding, above) this was the *requester's*; on writes it is the **operator's own machine**.
It is Zendesk's record and the tool is right to pass it through unaltered, but it means running
this server writes the operator's approximate physical location into model context on every call.
Worth a deliberate decision rather than a default, and it is the same decision as the read-side one.

## What was left changed

The test ticket, now **assigned, moved to the assigning agent's group, status `open`**, and
carrying one internal note. It could not be solved (Finding 8), so it is left open deliberately.

F3 is done: the id was registered in `CSA_ZD_ALLOWLIST_WRITE` by hand, which is the operator-granted
half of the allowlist doing its job, and the read/write asymmetry held — `READ=*` for triage,
`WRITE=` this one id.

## What F2 did not cover

- `reply_publicly` / `merge_tickets` (E5, G2) and `close_ticket` (G1) — still unbuilt, so the one
  thing never yet exercised end to end is a comment that actually reaches a customer.
- Whether the `group_id` side effect (Finding 6) is disruptive on this tenant. It changed queue
  ownership on a test ticket nobody routes; on a live ticket it may matter considerably more.
- Hidden text arriving via a `<style>` block rather than an inline attribute (H2). The fixture used
  inline styles only, so H2 is unchanged by this run: still an open, untested gap.
