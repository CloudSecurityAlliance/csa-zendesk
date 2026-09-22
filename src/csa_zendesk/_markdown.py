"""Zendesk HTML becomes Markdown here, and nowhere else.

DEC-018: when content crosses to a model, Markdown is the representation.
This module is the whole transform for csa-zendesk, and it is PURE - no HTTP,
no tokens, no filesystem - so it can be tested exhaustively offline.

Reads `html_body` and never `body`. Measured 2026-09-22: Zendesk's own
plain-text rendering is a naive tag strip - all five CSS-hidden probes survived
into `body` while the CSS that reveals them did not. The flattened form keeps
the payload and destroys the evidence, so it is strictly worse than the HTML.
"""  # noqa: D205

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

# Elements whose text is never shown to a reader. Removed before conversion so
# their content cannot arrive as ordinary prose. `script`/`style` are dropped by
# markdownify anyway; naming them here means the removal does not depend on that.
_NEVER_RENDERED = ("script", "style", "template", "noscript", "head")

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

#: Codepoints with no communicative purpose in prose. STRIPPED, never folded.
#: Measured 2026-09-22: `unidecode` also removes these, and additionally folds
#: `Pаypal` (Cyrillic а) to a byte-identical `Paypal` - completing the homoglyph
#: attack and destroying the evidence. Stripping is the same defang at zero
#: language cost: German, French, Russian, Japanese, Arabic and emoji are
#: unchanged, and so is `xʷməθkʷəy` (Musqueam).
_STRIP = (
    frozenset(range(0x200B, 0x200F))  # zero-width space/non-joiner/joiner/marks
    | {0x2060, 0xFEFF, 0x180E, 0x00AD}  # word joiner, BOM, Mongolian vowel sep, soft hyphen
    | frozenset(range(0x202A, 0x202F))  # bidi embedding and OVERRIDE
    | frozenset(range(0x2066, 0x206A))  # bidi isolates
    | frozenset(range(0xE0000, 0xE0080))  # tag characters ("invisible ink")
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


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


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
        element
        for element in soup.find_all(style=True)
        if element.attrs and _HIDDEN.search(str(element.attrs.get("style") or ""))
    ]
    hidden_texts = []
    for element in concealed:
        text = element.get_text(" ", strip=True)
        if text:
            hidden_texts.append(text)
        element.decompose()

    markdown = MarkdownConverter().convert_soup(soup)
    return strip_suspicious(markdown.strip()), [strip_suspicious(t) for t in hidden_texts]
