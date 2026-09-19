"""Ticket text reaches a model as data, never as instructions.

TODO A4 names prompt injection through ticket bodies as this project's primary
risk, and the whole-project design scheduled this wrapping for a later block
alongside the full tool surface. That sequencing stops being right the moment
a real ticket body reaches a model: anyone can email `support@`, so the queue
is the most obvious injection vector a support tool has. Read-only does not
make that safe - it makes it quieter, because an injected instruction cannot
act through *this* server but can act through every other tool the client has
loaded.

**Why character-level neutralisation, not substring matching.** A hostile
ticket body can write the closing marker in any case, with whitespace inside
it, more than once, or nested inside the opening marker. Searching for the
marker as a literal string and stripping or escaping just that occurrence
chases an unbounded set of disguises. Instead, `_neutralise` removes the
character class both markers are built from (`<`, `>`) from the ENTIRE text,
unconditionally. Once no `<` or `>` survives, the text cannot contain either
marker as a literal substring, by construction - regardless of case, spacing,
repetition or nesting, because none of those disguises change which
characters are present. The replacement characters (`‹`, `›`) are
visually close to the originals, so a reader can still see what was written;
nothing is deleted.

**Pure functions, no I/O.** This module knows nothing about HTTP, tokens, or
the filesystem - it only reshapes envelopes already in hand. `wrap_ticket`,
`wrap_comments` and `wrap_search` never mutate the caller's envelope
(`copy.deepcopy`, the same reason `backend.py` imports `copy`): the caller may
still hold, log or reuse the original.

**What gets wrapped, and why not everything.** Only strings a requester can
author: `subject`, `description`, `raw_subject`, each comment's
`body`/`html_body`/`plain_body`, a requester's or an author's `name`/`email`
(should a caller assemble one of those into the envelope), a tag a requester
added via email, and a string-valued custom field. Ids, timestamps, `status`,
`priority` and `public` are machine-set - an attacker cannot author them - so
they are left untouched; wrapping them would be noise exactly where the
wrapper's legibility matters most.
"""

from __future__ import annotations

import copy
from typing import Any

from .backend import Envelope

#: Built entirely from the two characters `_neutralise` strips out of
#: untrusted text, so no arrangement of that text - any case, any quantity,
#: any position, nested or not - can survive neutralisation and read as a real
#: marker. See the module docstring.
MARKER_OPEN = "<<<UNTRUSTED-ZENDESK-DATA>>>"
MARKER_CLOSE = "<<<END-UNTRUSTED-ZENDESK-DATA>>>"

#: `<`/`>` are what the markers are built from; the replacements are visually
#: close lookalikes, not deletions, so a reader can still see what was typed.
_ANGLE_BRACKETS = str.maketrans({"<": "‹", ">": "›"})

#: Requester-authored string fields on a ticket-shaped dict. Shared by
#: `wrap_ticket` and `wrap_search`, whose `results` entries are ticket objects.
_TICKET_STRING_FIELDS = ("subject", "description", "raw_subject")

#: Requester-authored string fields on a comment.
_COMMENT_STRING_FIELDS = ("body", "html_body", "plain_body")

#: Requester-authored string fields on a person object (a requester or an
#: author), should a caller assemble one into the envelope.
_PERSON_STRING_FIELDS = ("name", "email")


def _neutralise(text: str) -> str:
    """Replace every `<`/`>` in `text` with a lookalike character."""
    return text.translate(_ANGLE_BRACKETS)


def wrap(text: str, *, source: str) -> str:
    """Delimit `text` as untrusted content originating at `source`.

    `source` names where the text came from (e.g. `zendesk-ticket-42`) so a
    reader can attribute the content without granting it any authority. The
    hostile case - `text` containing either marker - is handled by
    `_neutralise` before the real markers are added, so the region this
    produces always has exactly one open and one close marker: the ones added
    here.
    """
    return f"{MARKER_OPEN} source={source}\n{_neutralise(text)}\n{MARKER_CLOSE}"


def _wrap_person(person: Any, *, source: str) -> None:
    """Wrap `name`/`email` on a person-shaped dict, in place, if present."""
    if not isinstance(person, dict):
        return
    for field in _PERSON_STRING_FIELDS:
        value = person.get(field)
        if isinstance(value, str):
            person[field] = wrap(value, source=f"{source}:{field}")


def _wrap_ticket_like(ticket: dict[str, Any], *, source: str) -> None:
    """Wrap the requester-authored fields of a ticket-shaped dict, in place.

    Shared by `wrap_ticket` (the `ticket` envelope) and `wrap_search` (each
    entry of `results`, which are ticket objects) so the two cannot drift on
    which fields count as untrusted.
    """
    for field in _TICKET_STRING_FIELDS:
        value = ticket.get(field)
        if isinstance(value, str):
            ticket[field] = wrap(value, source=source)

    tags = ticket.get("tags")
    if isinstance(tags, list):
        ticket["tags"] = [wrap(tag, source=f"{source}:tags") if isinstance(tag, str) else tag for tag in tags]

    custom_fields = ticket.get("custom_fields")
    if isinstance(custom_fields, list):
        for entry in custom_fields:
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            if isinstance(value, str):
                entry["value"] = wrap(value, source=f"{source}:custom_field:{entry.get('id')}")

    _wrap_person(ticket.get("requester"), source=f"{source}:requester")


def _ticket_source(ticket: dict[str, Any]) -> str:
    ticket_id = ticket.get("id")
    return f"zendesk-ticket-{ticket_id}" if ticket_id is not None else "zendesk-ticket"


def wrap_ticket(envelope: Envelope) -> Envelope:
    """Wrap the requester-authored fields of a `{"ticket": {...}}` envelope."""
    out = copy.deepcopy(envelope)
    ticket = out.get("ticket")
    if isinstance(ticket, dict):
        _wrap_ticket_like(ticket, source=_ticket_source(ticket))
    return out


def wrap_comments(envelope: Envelope) -> Envelope:
    """Wrap the requester-authored fields of a `{"comments": [...]}` envelope."""
    out = copy.deepcopy(envelope)
    comments = out.get("comments")
    if isinstance(comments, list):
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            comment_id = comment.get("id")
            source = f"zendesk-comment-{comment_id}" if comment_id is not None else "zendesk-comment"
            for field in _COMMENT_STRING_FIELDS:
                value = comment.get(field)
                if isinstance(value, str):
                    comment[field] = wrap(value, source=source)
            _wrap_person(comment.get("author"), source=f"{source}:author")
    return out


def wrap_search(envelope: Envelope) -> Envelope:
    """Wrap the requester-authored fields of each ticket in a search envelope."""
    out = copy.deepcopy(envelope)
    results = out.get("results")
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict):
                _wrap_ticket_like(result, source=_ticket_source(result))
    return out
