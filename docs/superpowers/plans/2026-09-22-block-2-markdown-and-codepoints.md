# Block 2: `html_body` becomes Markdown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Zendesk `html_body` to Markdown at the `Backend` seam, surfacing CSS-hidden text separately rather than dropping or silently keeping it, and strip Unicode codepoints that have no communicative purpose.

**Architecture:** A new pure module `_markdown.py` owns the whole transform: parse with BeautifulSoup once, strip suspicious codepoints, decompose hidden-looking elements (keeping their text), convert the remainder with `markdownify`, return `(markdown, hidden_texts)`. `ApiBackend`/`FakeBackend` call it so the library gets the behaviour without the MCP server — the seam rule this project has paid for four times. `_untrusted` keeps wrapping; `html_body` simply stops being HTML by the time it arrives.

**Tech Stack:** Python 3.10+, `markdownify>=1.2`, `beautifulsoup4>=4.12`, existing `httpx`. pytest, `mypy --strict`, ruff.

**Spec:** [DEC-018](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/DECISIONS.md), the bakeoff at [`research/html-to-markdown/README.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/tree/main/research/html-to-markdown), and [`FINDINGS-hidden-text.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/research/html-to-markdown/FINDINGS-hidden-text.md).

## Global Constraints

- **100% coverage**, `--cov-fail-under=100`. `# pragma: no cover` needs a reason on the line.
- **`mypy --strict`**, `ruff check .`, `ruff format --check src tests` all clean.
- **Nothing writes to stdout** — stdout is the JSON-RPC channel. `ruff` T20 enforces it in `src`.
- **No credential in any message, log, exception or `__repr__`.**
- **No network in tests** — `httpx.MockTransport` or `FakeBackend`.
- **No real subdomain or ticket id anywhere** — `scripts/check_public_safe.py` refuses them.
- **`markdownify` is the chosen converter** (bakeoff: 10/10 fidelity, BeautifulSoup-based so it shares the pre-pass's parse). Do not substitute.
- **Convert from `html_body`, never from `body`** — measured: Zendesk's plain-text rendering keeps the hidden payload and destroys the CSS that reveals it.
- **Raw-HTML passthrough must not survive** — `<script>` must not appear in output.

## Review Focus

1. **`html_body` absent or empty** — many comments have none; must not raise, must not emit an empty hidden block. *(Task 1)*
2. **Malformed HTML** — unclosed tags, stray `<`, a bare `&`. BeautifulSoup tolerates these; the transform must too. *(Task 1)*
3. **Hidden element containing only whitespace** — must produce no hidden block, or the 4-of-10-elements-are-empty measurement becomes noise in every ticket. *(Task 2)*
4. **Legitimate non-ASCII must survive stripping** — German, French, Russian, Japanese, Arabic, emoji. This is the failure mode four detectors have already had. *(Task 3)*
5. **A comment where hidden text is the whole body** — visible Markdown is empty; output must still be coherent rather than a bare hidden block with no context. *(Task 2)*

---

### Task 1: `_markdown.py` — convert HTML to Markdown

**Files:**
- Create: `src/csa_zendesk/_markdown.py`
- Modify: `pyproject.toml` (dependencies)
- Test: `tests/test_markdown.py`

**Interfaces:**
- Produces: `to_markdown(html: str) -> tuple[str, list[str]]` returning `(markdown, hidden_texts)`. Task 1 returns `[]` for the second element always; Task 2 fills it.

- [ ] **Step 1: Add the dependencies**

In `pyproject.toml`, change the `dependencies` list to:

```toml
# markdownify + beautifulsoup4, not markitdown or pandoc: the bakeoff
# (research/html-to-markdown/) measured all four real converters leaking
# CSS-hidden text identically, so the choice is fidelity and packaging only.
# markdownify ties at 10/10 fidelity and is BeautifulSoup-based, so the
# hidden-element pre-pass and the conversion share ONE parse and one
# dependency. markitdown wraps markdownify and pulls in ~112MB of ML runtime
# (magika -> onnxruntime + numpy) to detect a format we read from a field
# named html_body.
dependencies = ["httpx>=0.27", "markdownify>=1.2", "beautifulsoup4>=4.12"]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_markdown.py
import pytest

from csa_zendesk._markdown import to_markdown


def test_basic_html_becomes_markdown():
    md, hidden = to_markdown("<p>Hello <strong>world</strong></p>")
    assert "Hello" in md and "**world**" in md
    assert "<p>" not in md
    assert hidden == []


def test_links_keep_their_destination():
    # Dropping the href was the reason "just use Zendesk's plain body" was
    # rejected: a mailto: or https: target is often the thing an agent needs.
    md, _ = to_markdown('<p>See <a href="https://example.invalid/d">the doc</a>.</p>')
    assert "https://example.invalid/d" in md
    assert "the doc" in md


def test_script_does_not_survive():
    md, _ = to_markdown("<p>hi</p><script>alert(1)</script>")
    assert "alert(1)" not in md


def test_empty_and_absent_html_are_not_errors():
    for value in ("", "   "):
        md, hidden = to_markdown(value)
        assert md.strip() == ""
        assert hidden == []


@pytest.mark.parametrize(
    "html",
    ["<p>unclosed", "<div><span>stray < bracket</div>", "a &amp b &", "<<>>"],
)
def test_malformed_html_does_not_raise(html):
    # Real ticket HTML is mail-client output. BeautifulSoup tolerates this;
    # the transform must not add a failure mode BeautifulSoup does not have.
    md, hidden = to_markdown(html)
    assert isinstance(md, str) and isinstance(hidden, list)
```

- [ ] **Step 3: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_markdown.py -q`
Expected: FAIL — `ModuleNotFoundError: csa_zendesk._markdown`

- [ ] **Step 4: Write `_markdown.py`**

```python
"""Zendesk HTML becomes Markdown here, and nowhere else.

DEC-018: when content crosses to a model, Markdown is the representation.
This module is the whole transform for csa-zendesk, and it is PURE - no HTTP,
no tokens, no filesystem - so it can be tested exhaustively offline.

Reads `html_body` and never `body`. Measured 2026-09-22: Zendesk's own
plain-text rendering is a naive tag strip - all five CSS-hidden probes survived
into `body` while the CSS that reveals them did not. The flattened form keeps
the payload and destroys the evidence, so it is strictly worse than the HTML.
"""
from __future__ import annotations

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

# Elements whose text is never shown to a reader. Removed before conversion so
# their content cannot arrive as ordinary prose. `script`/`style` are dropped by
# markdownify anyway; naming them here means the removal does not depend on that.
_NEVER_RENDERED = ("script", "style", "template", "noscript", "head")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def to_markdown(html: str) -> tuple[str, list[str]]:
    """Convert `html` to Markdown. Returns `(markdown, hidden_texts)`.

    `hidden_texts` is always empty here; Task 2 fills it.
    """
    soup = _soup(html)
    for element in soup.find_all(_NEVER_RENDERED):
        element.decompose()
    markdown = MarkdownConverter().convert_soup(soup)
    return markdown.strip(), []
```

- [ ] **Step 5: Run the tests and the gates**

```bash
.venv/bin/python -m pytest tests/test_markdown.py -q
.venv/bin/python -m pytest -q --cov=csa_zendesk --cov-report=term-missing
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy
```
Expected: all pass, coverage 100%.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(markdown): html_body becomes Markdown, in a pure module"
```

---

### Task 2: The hidden-element pre-pass

**Files:**
- Modify: `src/csa_zendesk/_markdown.py`
- Test: `tests/test_markdown.py`

**Interfaces:**
- Consumes: `to_markdown` from Task 1.
- Produces: `to_markdown` now returns real `hidden_texts`. Also `HIDING_RULES: tuple[str, ...]` for the tests to enumerate.

**The measured facts this task must respect.** `markdownify` and `html2text` both emit `display:none` text as ordinary prose — structural, not a bug: they are DOM walkers with no CSS cascade. A naive pre-pass closes `display:none` and zero-size and **still misses white-on-white**, because the background lives on an ancestor. Two of three is what a control that looks like it works looks like, so the residual is documented, not hidden.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_markdown.py
from csa_zendesk._markdown import HIDING_RULES


@pytest.mark.parametrize(
    "style",
    ["display:none", "display: none", "visibility:hidden", "font-size:0",
     "height:0", "max-height:0", "opacity:0", "left:-9999px", "text-indent:-9999px"],
)
def test_hidden_text_is_surfaced_not_emitted_as_prose(style):
    html = f'<p>Refund please.</p><div style="{style}">SECRET INSTRUCTION</div>'
    md, hidden = to_markdown(html)
    assert "Refund please." in md
    assert "SECRET INSTRUCTION" not in md, "hidden text leaked into the visible Markdown"
    assert hidden == ["SECRET INSTRUCTION"]


def test_hidden_elements_with_no_text_produce_nothing():
    # Measured: 4 of 10 hidden elements in the real corpus are EMPTY - spacers
    # and tracking pixels. Reporting them would put a hidden block on every
    # newsletter for no information at all.
    md, hidden = to_markdown('<p>hi</p><div style="display:none"></div>'
                             '<span style="font-size:0">   </span>')
    assert hidden == []
    assert "hi" in md


def test_a_body_that_is_entirely_hidden_still_returns_coherently():
    md, hidden = to_markdown('<div style="display:none">only this</div>')
    assert md.strip() == ""
    assert hidden == ["only this"]


def test_white_on_white_is_a_KNOWN_GAP_and_still_leaks():
    # Recorded, not fixed. Deciding whether text is the same colour as its
    # background needs the ANCESTOR's background, i.e. the cascade. This test
    # pins the current behaviour so that closing the gap is a deliberate,
    # visible change rather than an accident.
    html = ('<div style="background:#ffffff">'
            '<span style="color:#ffffff">STILL LEAKS</span></div>')
    md, hidden = to_markdown(html)
    assert "STILL LEAKS" in md
    assert hidden == []


def test_every_hiding_rule_is_exercised_by_a_test():
    # Anti-vacuity: a rule added to HIDING_RULES without a case above would
    # otherwise be untested and look covered.
    exercised = {"display", "visibility", "font-size", "height", "opacity", "left", "text-indent"}
    for rule in HIDING_RULES:
        assert any(token in rule for token in exercised), f"{rule} has no test"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_markdown.py -q`
Expected: FAIL — `ImportError: cannot import name 'HIDING_RULES'`

- [ ] **Step 3: Implement the pre-pass**

Add to `_markdown.py`, above `to_markdown`:

```python
import re

#: Inline-style declarations that mean "a reader will not see this".
#: INLINE STYLES ONLY, deliberately: a `<style>` block's selectors need a
#: cascade to resolve, which is the open problem this does not pretend to
#: solve. Under-reporting is the honest failure direction.
HIDING_RULES: tuple[str, ...] = (
    r"display\s*:\s*none",
    r"visibility\s*:\s*hidden",
    r"font-size\s*:\s*0(?![.\d])",
    r"(?:max-)?height\s*:\s*0(?![.\d])",
    r"opacity\s*:\s*0(?![.\d])",
    r"(?:left|top|text-indent)\s*:\s*-\d{3,}",
)
_HIDDEN = re.compile("|".join(HIDING_RULES), re.I)
```

Replace `to_markdown`'s body with:

```python
def to_markdown(html: str) -> tuple[str, list[str]]:
    """Convert `html` to Markdown, separating text a reader would not see.

    Returns `(markdown, hidden_texts)`. Hidden text is REMOVED from the Markdown
    and returned alongside it - never silently dropped, never emitted as
    ordinary prose. Concealment is the signal; the caller decides what to do
    with it.

    KNOWN GAP: white-on-white is not detected. Whether text matches its
    background needs the ancestor's background - the cascade - and a rule on the
    element's own `style` cannot see it. Measured: a naive pre-pass closes
    display:none and zero-size and misses this one.
    """
    soup = _soup(html)
    for element in soup.find_all(_NEVER_RENDERED):
        element.decompose()

    # Collect first, THEN decompose. Decomposing inside find_all() invalidates
    # elements the iterator has not reached, which comes back as attrs=None
    # several elements later.
    concealed = [
        element for element in soup.find_all(style=True)
        if element.attrs and _HIDDEN.search(element.attrs.get("style") or "")
    ]
    hidden_texts = []
    for element in concealed:
        text = element.get_text(" ", strip=True)
        if text:
            hidden_texts.append(text)
        element.decompose()

    markdown = MarkdownConverter().convert_soup(soup)
    return markdown.strip(), hidden_texts
```

- [ ] **Step 4: Run the tests and the gates**

```bash
.venv/bin/python -m pytest -q --cov=csa_zendesk --cov-report=term-missing
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy
```

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(markdown): surface hidden text rather than dropping or emitting it"
```

---

### Task 3: Strip codepoints with no communicative purpose

**Files:**
- Modify: `src/csa_zendesk/_markdown.py`
- Test: `tests/test_markdown.py`

**Interfaces:**
- Produces: `strip_suspicious(text: str) -> str`, applied inside `to_markdown` to both the Markdown and each hidden text.

**Why stripping and not folding.** Measured: `unidecode` neutralises zero-width, bidi and tag characters — and **completes** homoglyph attacks, folding `Pаypal` (Cyrillic а) to a byte-identical `Paypal` and destroying the only evidence. Stripping gets the same defang at **zero language cost**: German, French, Russian, Japanese, Arabic and emoji all pass through unchanged. **This task adds no homoglyph detection** — that rule flags Indigenous orthographies and IPA and is not ready.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_markdown.py
from csa_zendesk._markdown import strip_suspicious

ZWSP = "​"


@pytest.mark.parametrize(
    "text,gone",
    [
        (ZWSP.join("Alexander"), ZWSP),          # the observed signature case
        ("invoice‮fdp.exe", "‮"),      # bidi override
        ("hello\U000e0041\U000e0042", "\U000e0041"),  # tag characters
        ("a﻿b", "﻿"),                  # BOM mid-text
        ("x\u0000y", "\u0000"),                  # control character
    ],
)
def test_codepoints_with_no_communicative_purpose_are_removed(text, gone):
    assert gone not in strip_suspicious(text)


@pytest.mark.parametrize(
    "text",
    [
        "Sehr geehrte Damen und Herren",
        "Château, naïve, résumé",
        "Здравствуйте",
        "パスワード",
        "أحتاج إلى مساعدة",
        "Thanks! \U0001f642\U0001f44d",
        "xʷməθkʷəy",     # Musqueam - MUST survive
        "Pаypal",                            # homoglyph - NOT our job here
    ],
)
def test_legitimate_text_is_untouched(text):
    # Four detectors before this one had non-English content as their dominant
    # failure mode. Stripping must have ZERO language cost - measured.
    assert strip_suspicious(text) == text


def test_to_markdown_strips_in_both_the_visible_and_hidden_output():
    html = (f'<p>Hi {ZWSP}there</p>'
            f'<div style="display:none">bad{ZWSP}text</div>')
    md, hidden = to_markdown(html)
    assert ZWSP not in md
    assert hidden and ZWSP not in hidden[0]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_markdown.py -q`
Expected: FAIL — `ImportError: cannot import name 'strip_suspicious'`

- [ ] **Step 3: Implement `strip_suspicious`**

Add to `_markdown.py`:

```python
#: Codepoints with no communicative purpose in prose. STRIPPED, never folded.
#: Measured 2026-09-22: `unidecode` also removes these, and additionally folds
#: `Pаypal` (Cyrillic а) to a byte-identical `Paypal` - completing the homoglyph
#: attack and destroying the evidence. Stripping is the same defang at zero
#: language cost: German, French, Russian, Japanese, Arabic and emoji are
#: unchanged, and so is `xʷməθkʷəy` (Musqueam).
_STRIP = (
    frozenset(range(0x200B, 0x200F))        # zero-width space/non-joiner/joiner/marks
    | {0x2060, 0xFEFF, 0x180E, 0x00AD}      # word joiner, BOM, Mongolian vowel sep, soft hyphen
    | frozenset(range(0x202A, 0x202F))      # bidi embedding and OVERRIDE
    | frozenset(range(0x2066, 0x206A))      # bidi isolates
    | frozenset(range(0xE0000, 0xE0080))    # tag characters ("invisible ink")
    | frozenset(c for c in range(0x20) if c not in (0x09, 0x0A, 0x0D))  # controls
)


def strip_suspicious(text: str) -> str:
    """Remove codepoints that carry no meaning a reader could receive.

    NOT a homoglyph check. A rule that flags mixed scripts within a word also
    flags Indigenous orthographies (Musqueam contains a Greek theta, because
    IPA-derived characters are its standard written form) and IPA
    transcriptions. That rule is measured at 6-of-8 false positives against
    legitimate content and is deliberately absent here.
    """
    return "".join(ch for ch in text if ord(ch) not in _STRIP)
```

Then apply it in `to_markdown`, replacing the final two lines:

```python
    markdown = MarkdownConverter().convert_soup(soup)
    return strip_suspicious(markdown.strip()), [strip_suspicious(t) for t in hidden_texts]
```

- [ ] **Step 4: Run the tests and the gates**

```bash
.venv/bin/python -m pytest -q --cov=csa_zendesk --cov-report=term-missing
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy
```

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(markdown): strip codepoints with no communicative purpose, and nothing else"
```

---

### Task 4: Wire it into the Backend seam

**Files:**
- Modify: `src/csa_zendesk/backend.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `to_markdown` from Tasks 1–3.
- Produces: `get_ticket`, `list_comments` and `search_tickets` envelopes where every `html_body` has been replaced by Markdown, and a sibling `hidden_text` key appears only when there is hidden text.

**The seam rule.** This goes in `ApiBackend`/`FakeBackend`, **not** `server.py`. The library is callable without the MCP server, and this project has paid four separate times for putting a control in the delivery layer.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_backend.py
ZWSP = "​"


def _comment_html(html):
    return {"comments": [{"id": 1, "public": True, "body": "plain", "html_body": html}]}


def test_list_comments_returns_markdown_not_html():
    def handler(request):
        return httpx.Response(200, json=_comment_html("<p>Hello <strong>world</strong></p>"))

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert "**world**" in cm["html_body"]
    assert "<p>" not in cm["html_body"]


def test_hidden_text_arrives_in_its_own_key_and_not_in_the_body():
    def handler(request):
        return httpx.Response(200, json=_comment_html(
            '<p>Refund please.</p><div style="display:none">SECRET</div>'))

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert "SECRET" not in cm["html_body"]
    assert cm["hidden_text"] == ["SECRET"]


def test_no_hidden_key_when_there_is_no_hidden_text():
    # A key present on every comment with an empty list is noise on ~96% of them.
    def handler(request):
        return httpx.Response(200, json=_comment_html("<p>ordinary</p>"))

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert "hidden_text" not in cm


def test_plain_body_is_left_alone():
    # `body` is Zendesk's own tag strip and is NOT the source of truth here,
    # but it is also not ours to rewrite - callers may rely on it verbatim.
    def handler(request):
        return httpx.Response(200, json=_comment_html("<p>x</p>"))

    cm = ApiBackend(_client(handler)).list_comments(ticket_id=1)["comments"][0]
    assert cm["body"] == "plain"


def test_a_ticket_description_is_converted_too():
    def handler(request):
        return httpx.Response(200, json={"ticket": {
            "id": 1, "subject": "s", "description": "d",
            "html_body": f"<p>tick{ZWSP}et</p>"}})

    t = ApiBackend(_client(handler)).get_ticket(ticket_id=1)["ticket"]
    assert ZWSP not in t["html_body"]
    assert "<p>" not in t["html_body"]


def test_the_fake_converts_too():
    # A fake that returned raw HTML would let every markdown assertion pass
    # while doing nothing in production - the reason the fake enforces the
    # extension and empty-upload rules too.
    fake = FakeBackend()
    fake.tickets[1] = {"id": 1, "html_body": "<p>hi <em>there</em></p>"}
    out = fake.get_ticket(ticket_id=1)["ticket"]
    assert "*there*" in out["html_body"] and "<em>" not in out["html_body"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_backend.py -q -k markdown or hidden`
Expected: FAIL — `html_body` still contains `<p>`.

- [ ] **Step 3: Implement the envelope transform**

Add near the other helpers in `backend.py`:

```python
def _convert_html_bodies(envelope: Envelope) -> Envelope:
    """Replace every `html_body` in `envelope` with Markdown, in place.

    Walks the whole envelope rather than naming ticket/comment shapes, because
    `html_body` appears on tickets, comments, audit events and search results,
    and a shape-specific walk would silently miss the next one.

    Adds a sibling `hidden_text` list ONLY when there is hidden text - a key
    present on every comment with an empty list is noise on the ~96% that carry
    none (measured).

    Applied at the Backend seam, not in `server.py`: the library is callable
    without the MCP server, and a control in the delivery layer is one a library
    consumer does not get.
    """
    if isinstance(envelope, dict):
        html = envelope.get("html_body")
        if isinstance(html, str):
            markdown, hidden = _markdown.to_markdown(html)
            envelope["html_body"] = markdown
            if hidden:
                envelope["hidden_text"] = hidden
        for value in envelope.values():
            _convert_html_bodies(value)  # type: ignore[arg-type]
    elif isinstance(envelope, list):
        for item in envelope:
            _convert_html_bodies(item)
    return envelope
```

Import `from . import _markdown` at the top. Then wrap the three read methods' returns in **both** backends — for example in `ApiBackend.get_ticket`:

```python
        return _convert_html_bodies(self._http.get(f"/api/v2/tickets/{_path_id(ticket_id, name='ticket_id')}"))
```

Do the same for `ApiBackend.list_comments`, `ApiBackend.search_tickets`, and the matching `FakeBackend` methods. Envelopes are already deep-copied by `FakeBackend`, so mutating in place does not leak into its store — confirm by running the existing `test_fake_backend_does_not_hand_out_its_own_state` style tests.

- [ ] **Step 4: Run the full suite and the gates**

```bash
.venv/bin/python -m pytest -q --cov=csa_zendesk --cov-report=term-missing
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy
.venv/bin/python scripts/check_public_safe.py
.venv/bin/python scripts/check_boundaries.py
```

Existing `_untrusted` tests may need their fixtures updated — `html_body` is no longer HTML by the time it is wrapped, so `_MARKUP_KEYS`' rationale changes. Do **not** delete those tests; update the fixtures and keep the assertions.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(backend): html_body arrives as Markdown, hidden text arrives beside it"
```

---

### Task 5: The record

**Files:**
- Modify: `src/csa_zendesk/_untrusted.py` (the `_MARKUP_KEYS` comment), `README.md`, `CHANGELOG.md`, `TODO.md`, `analysis/API-SURFACE.md`

- [ ] **Step 1: Correct `_untrusted.py`'s reasoning**

`_MARKUP_KEYS` exists because `html_body` "contains a literal `<` in EVERY comment". That stops being true. Update the comment to say `html_body` is now Markdown before `_untrusted` sees it, that the `note_on_change` suppression is retained because Markdown still contains characters `_neutralise` touches, and that the module docstring's "**`html_body` stops being HTML**" paragraph now describes Task 4 rather than neutralisation.

- [ ] **Step 2: `README.md`**

Document, where the tools are described: `html_body` is Markdown, not HTML; a `hidden_text` key appears beside it when a comment contained text a reader would not see; and the **white-on-white known gap**, stated plainly rather than as a footnote.

- [ ] **Step 3: `CHANGELOG.md`**

An `[Unreleased]` entry. Say what is **not** done: no homoglyph detection (the rule fails 6 of 8 legitimate fixtures), no classification hook, no attachment reading.

- [ ] **Step 4: `TODO.md`**

Close the DEC-018 counter-example line. Add the white-on-white gap and the `<style>`-block limitation as open items.

- [ ] **Step 5: Full gates, then commit**

```bash
.venv/bin/python -m pytest -q --cov=csa_zendesk --cov-report=term-missing
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy && .venv/bin/python scripts/check_public_safe.py
.venv/bin/python -m bandit -r src -q && .venv/bin/python -m pip_audit --skip-editable
git add -A && git commit -m "docs: html_body is Markdown now, and what that does not cover"
```

---

## Self-Review

**Spec coverage.** DEC-018's three obligations: raw-HTML passthrough disabled (Task 1, `<script>` test); a link/image policy — **partially**, links keep destinations (Task 1) but no rewriting or stripping is applied, which is recorded as out of scope rather than done; provenance labelling mandatory — unchanged, `_untrusted` still wraps everything. The bakeoff's converter choice is Task 1. The hidden-text measurements drive Task 2. The ASCII-folding finding drives Task 3's strip-not-fold decision.

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Every code step carries the actual code.

**Type consistency.** `to_markdown(html: str) -> tuple[str, list[str]]` is introduced in Task 1 and unchanged through Task 4. `strip_suspicious(text: str) -> str` is Task 3 only. `_convert_html_bodies(envelope: Envelope) -> Envelope` uses the existing `Envelope = dict[str, Any]` alias.

**Review Focus.** All five have owning tasks: absent/empty HTML and malformed HTML in Task 1; whitespace-only hidden elements and all-hidden bodies in Task 2; legitimate non-ASCII survival in Task 3.

**The risk I would flag to a reviewer.** Task 4 mutates envelopes in place during a recursive walk. `FakeBackend` deep-copies on the way out, so its store is safe — but `ApiBackend` returns what `_http` handed it, and if anything else ever holds a reference to that dict, the conversion becomes visible to that holder. Nothing does today. The test to keep honest is whichever one proves the fake does not hand out its own state.
