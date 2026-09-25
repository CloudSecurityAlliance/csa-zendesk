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

## Finding 3 — `html_body` is neutralised without being labelled neutralised

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

## What was left changed

The test ticket, still open. It was not worked through the write surface: `CSA_ZD_ALLOWLIST_WRITE`
is unset, so every write correctly refused — the allowlist doing exactly its job (F3). Exercising
the write path on this fixture needs the id registered by hand first, which is a separate run.

## What F2 did not cover

- The write path on an email-originated ticket, and specifically the `comment.public` inheritance
  trap: on this fixture the first comment **is** public, so every later comment defaults to public
  unless forced. That is the sharpest possible test of `add_internal_note`'s hardcoded
  `public: false`, and it remains untested live. It needs F3 done first.
- `reply_publicly` / `merge_tickets` (E5, G2) and `close_ticket` (G1) — still unbuilt.
- Hidden text arriving via a `<style>` block rather than an inline attribute (H2). The fixture used
  inline styles only, so H2 is unchanged by this run: still an open, untested gap.
