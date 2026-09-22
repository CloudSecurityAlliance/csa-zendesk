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
