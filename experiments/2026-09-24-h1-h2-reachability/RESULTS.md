# H1 / H2 reachability — are our known gaps reachable through email? 2026-09-24

`_markdown.py` ships with two recorded gaps:

- **H1** — white-on-white text is not detected, because deciding that text matches its background
  needs the resolved background of the ancestor chain.
- **H2** — a `<style>` block's selectors are invisible, because resolving one against the document
  needs a CSS cascade the module does not build.

Both were recorded as absences. **Neither had been checked against the channel that actually feeds
this tool.** A gap that cannot be reached through email is a different thing from one that can.

**Method.** A second HTML-only email, carrying three sentinels — text hidden by a class selector in
a `<style>` block, text zero-sized by a class, and white text on a white background — plus a
`<script>` body and an HTML comment. Read back through `list_comments`, then the **raw** `html_body`
fetched directly from the API with the same credential, to see what Zendesk hands us *before* our
conversion runs.

That last step is what turns this from inference into measurement.

---

## Finding 1 — Zendesk inlines the CSS, so H2 is not reachable through the email channel

Both style-block sentinels came back in `hidden_text`, correctly removed from the Markdown — even
though our converter has no cascade and inspects only inline `style` attributes.

The raw `html_body` explains why:

```
<style> block survives into html_body?  False
inline style= attributes present?       True

  .concealed { display: none }   ->   <p style="display: none;" …>
  .tiny      { font-size: 0px }  ->   <span style="font-size: 0px;">
```

**Zendesk resolves the cascade during email ingestion and writes the result back as inline styles.**
By the time the HTML reaches us, an H2-shaped concealment has already been converted into the
H1-shaped inline case our rules do catch. (The tenant also tags such tickets `skip_css`, which is
the visible trace of that processing step.)

So H2 is real as written — the module genuinely cannot resolve a selector — but **unreachable on
this path**, because nothing ever asks it to.

Three caveats, because this is the kind of finding that gets over-claimed:

1. **Channel-specific.** Measured for inbound email. A ticket created through the API, or another
   channel, may carry HTML Zendesk never processed. H2 stays open for those.
2. **Vendor behaviour, not a contract.** Zendesk can change this whenever it likes, and nothing
   would tell us. It is not a guarantee we can build on.
3. **Possibly configurable.** The `skip_css` tag implies a processing step that may be switchable.
   If it is ever turned off, H2 becomes reachable again silently.

H2 should therefore be **narrowed rather than closed**, with the reason recorded — otherwise the
next person reads "not reachable" and deletes the handling.

## Finding 2 — H1 is reachable, and it leaked exactly as predicted

The white-on-white sentinel arrived in `html_body` **inline, as ordinary prose**, and did not appear
in `hidden_text`.

```
raw:  <p style="color: #ffffff;" …>SENTINEL-WHITEONWHITE-…
```

Confirmed live. H1 is not theoretical.

**But the interesting part is why it leaked**, because it is not the reason H1 records. The hiding
rule here *is* an inline style — precisely the kind the converter inspects. The text survived
because `HIDING_RULES` contains no colour rule at all.

That is a **deliberate choice, not an inability**. Matching `color:#ffffff` inline would have caught
this sentinel today, with two lines of regex. It was rejected because a colour is only concealment
*relative to an ancestor's background*, and the corpus study that informed these rules found a naive
colour rule produced **82.6% of all detections** — overwhelmingly on ordinary marketing email that
sets a colour and a matching background together, legitimately.

So H1's real shape is sharper than its current wording: *we can see the rule, we cannot see what it
is relative to, and guessing costs more than missing.* Worth restating in those terms, because "not
detected" reads like a capability gap when it is a false-positive trade.

## Finding 3 — Zendesk strips `<script>` and HTML comments before we see them

Neither the script body nor the comment sentinel appears anywhere: not in the raw `html_body`, not
in `body`, not in `plain_body`.

This confirms live what Block 2's whole-branch review found by reasoning: of `_NEVER_RENDERED`'s
five members, **`script` is not load-bearing** — the only one with a test behind it, and the one
Zendesk already handles on this path. `template`, `noscript` and `head` are the members doing real
work and none of them had a test.

Keep `script` regardless. It costs nothing, and points 1–3 above apply to it identically: this is
one channel's behaviour on one tenant today, not a contract.

---

## What this changes

| | Before | After |
|---|---|---|
| H1 | recorded gap, untested | **confirmed reachable**, and better understood as a false-positive trade rather than a blind spot |
| H2 | recorded gap, untested | **not reachable via email**, because Zendesk pre-inlines; still open for other channels |
| `_NEVER_RENDERED` | `script` untested-but-assumed-useful | `script` confirmed redundant *on this path*; the untested three still carry the load |

## What this did not cover

- API-created tickets and non-email channels, which is exactly where H2 remains live.
- Whether `skip_css` can be disabled, and what the HTML looks like if it is. That decides whether
  Finding 1 is stable or incidental.
- Any hiding technique not in the fixture — `clip-path`, `opacity` on an ancestor, off-screen
  positioning via a class, `visibility:hidden` inherited rather than set.
