"""Ticket text reaches a model as data, never as instructions.

TODO A4 names prompt injection through ticket bodies as this project's primary
risk, and the whole-project design scheduled this wrapping for a later block
alongside the full tool surface. That sequencing stops being right the moment
a real ticket body reaches a model: anyone can email `support@`, so the queue
is the most obvious injection vector a support tool has. Read-only does not
make that safe - it makes it quieter, because an injected instruction cannot
act through *this* server but can act through every other tool the client has
loaded.

**Wrap everything, then name the exceptions - not the other way round.** An
earlier version of this module named the fields it knew a requester could
author (`subject`, `description`, a comment's `body`, ...) and left every
other string in the envelope raw. That is an allowlist over a passthrough
envelope `ApiBackend` returns unfiltered (ADR-002) - so it fails OPEN: any
field Zendesk documents that this module's author did not think of, and any
endpoint whose shape differs even slightly, reaches the model unwrapped.
Concretely, that version missed `ticket["fields"]` (an alias for
`custom_fields` - the same requester text, shipped raw a second time),
`via.source.from.name`/`.address` (the actual requester identity on an
email-created ticket), `satisfaction_rating.comment`, a comment attachment's
`file_name`, `metadata.system.client`, `external_id`, and every string field
on a `user`/`organization` object that an unconstrained search query can
return instead of a ticket.

`_walk_dict`/`_walk_list` invert this: they recurse through the WHOLE
envelope and wrap every string they find, except a short, explicit denylist
of keys Zendesk itself sets (`_is_machine_set`, below). A vendor field this
module has never heard of now arrives wrapped by default. **Over-wrapping a
machine-set string is noise; under-wrapping a requester-set one is the
vulnerability this module exists to close - when in doubt, wrap.** Do not
"tidy" this back into a field allowlist; that is the defect this rewrite
fixes, not a style preference.

**Why character-level neutralisation, not substring matching.** A hostile
ticket body can write the closing marker in any case, with whitespace inside
it, more than once, or nested inside the opening marker. Searching for the
marker as a literal string and stripping or escaping just that occurrence
chases an unbounded set of disguises. Instead, `_neutralise` removes the
character class both markers are built from (`<`, `>`) from the ENTIRE text,
unconditionally. No codepoint below 0x11000 (there is nothing above it to
check) maps to either character under this table, so no substitution can ever
reconstruct one out of harmless halves; and `str.translate` makes a single
pass over the input and never re-scans its own output, unlike a chain of
`str.replace` calls, so a replacement cannot itself introduce a new match for
a later step to miss. Once no `<` or `>` survives, the text cannot contain
either marker as a literal substring, by construction - regardless of case,
spacing, repetition or nesting, because none of those disguises change which
characters are present. The replacement characters (`‹`, `›`) are
visually close to the originals, so a reader can still see what was written;
nothing is deleted.

**The boundary of that guarantee.** It is "no `<` or `>` survives", not "no
marker can survive Unicode normalisation". A full-width `＜＜＜`
("＜＜＜") is a different codepoint from `<` and passes `_neutralise`
untouched; NFKC-normalising the output afterwards would fold it back to
`<<<` and could reconstruct a marker. Nothing in this package normalises
Unicode today, so this does not bite in this codebase, but a caller who adds
normalisation downstream of `wrap()` inherits this boundary and needs to
normalise BEFORE wrapping, not after.

**Wrapping is not idempotent.** Calling `wrap()` (directly or via `wrap_*`) on
text that is already wrapped neutralises the previous call's own markers -
they are `<`/`>` sequences like any other - and encloses the mess in a new
pair. Every value must be wrapped exactly once; Task 5 wraps a tool's
response as the last step before it leaves this library, never earlier.

**`html_body` stops being HTML.** Neutralising `<`/`>` turns `<div>` into
`‹div›` - the wrapped value is no longer parseable markup. That is
the intended effect (an HTML tag is exactly the kind of structure an
injection would exploit a model's markup-awareness with), not a bug to fix
later.

**Pure functions, no I/O.** This module knows nothing about HTTP, tokens, or
the filesystem - it only reshapes envelopes already in hand. `wrap_ticket`,
`wrap_comments` and `wrap_search` never mutate the caller's envelope: `_walk_dict`
and `_walk_list` build a fresh dict or list at every level they recurse
through rather than mutating the one they were given, so the "never mutate"
property holds by construction rather than needing a separate `copy.deepcopy`
pass (contrast `backend.py`, which mutates nothing either but achieves it by
deep-copying because its methods hand envelopes out rather than reshaping
them in a single pass).
"""

from __future__ import annotations

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

#: Keys whose value Zendesk itself sets - never a requester - so `_walk_dict`
#: leaves them alone even though it wraps any other string by default.
#:
#: The test for membership: a key belongs here when a CONSUMER COMPARES its
#: value rather than reads it - an enum, a discriminator, an id, a timestamp,
#: a URL. `result_type` (OAS: the `ticket`/`user`/`organization`/`group`
#: discriminator on a search result) and `role` (a user's `end-user`/`agent`/
#: `admin` enum) are exactly this: code branches or filters on them, so
#: wrapping breaks the switch rather than merely costing legibility. Anything
#: a human wrote - a name, a comment, a subject line - stays wrapped, even
#: when it happens to look enum-like.
#:
#: Deliberately NOT a `*_id`-suffix rule. `external_id` ends the same way and
#: is requester/integration-authored text (OAS: "Unique identifier of the
#: external resource ... string"), not a foreign-key reference - it MUST be
#: wrapped, not denylisted. Every genuine foreign-key id field Zendesk returns
#: (`requester_id`, `assignee_id`, `group_id`, `organization_id`, `brand_id`,
#: ...) is an integer, which `_walk_dict` already leaves untouched by type
#: (only `str` values are ever passed to `wrap`) - so this set only needs to
#: name the STRING-typed fields that are machine-set, not every field whose
#: name merely contains "id". Extend this set by naming a key explicitly,
#: never by adding a pattern: a pattern acquires members the vendor adds
#: without anyone deciding.
_MACHINE_SET_KEYS = frozenset({"id", "url", "type", "status", "priority", "public", "result_type", "role"})


def _is_machine_set(key: str) -> bool:
    # `_at`-suffixed keys are Zendesk's uniform timestamp convention
    # (created_at, updated_at, due_at, solved_at, started_at, ...) - a fixed
    # naming pattern the vendor controls, never free text a requester wrote.
    return key in _MACHINE_SET_KEYS or key.endswith("_at")


def _neutralise(text: str) -> str:
    """Replace every `<`/`>` in `text` with a lookalike character."""
    return text.translate(_ANGLE_BRACKETS)


def wrap(text: str, *, source: str) -> str:
    """Delimit `text` as untrusted content originating at `source`.

    `source` names where the text came from (e.g. `zendesk-ticket-42.subject`)
    so a reader can attribute the content without granting it any authority.
    It is neutralised and newline-stripped exactly as `text` is: every caller
    in this codebase today builds it from machine ids and dotted field names,
    so it cannot carry a marker in practice, but `wrap` is the published
    primitive Task 5 calls directly, and an un-neutralised interpolation
    sitting right next to the marker it introduces is exactly the mistake this
    module exists to prevent in the text itself.

    When neutralisation actually changes `text` or `source`, a `(neutralised)`
    note is appended to the header - so a reader can tell a genuine escape
    attempt from a body that happens to contain `‹`/`›` on its own,
    which would otherwise be indistinguishable from one.
    """
    safe_text = _neutralise(text)
    safe_source = _neutralise(source).replace("\n", " ").replace("\r", " ")
    note = " (neutralised)" if safe_text != text or safe_source != source else ""
    return f"{MARKER_OPEN} source={safe_source}{note}\n{safe_text}\n{MARKER_CLOSE}"


def _walk_list(node: list[Any], *, path: str) -> list[Any]:
    """Return a new list with every string in `node` wrapped, recursively."""
    result: list[Any] = []
    for index, item in enumerate(node):
        # A list item carries no key of its own to check against
        # `_is_machine_set`, so a bare string here is always wrapped - in
        # every shape this package has seen, a scalar sitting directly in a
        # list (a tag, a redaction target) is requester-authored text, never
        # a machine-set id or enum (those always arrive as dict values).
        item_path = f"{path}[{index}]"
        if isinstance(item, dict):
            # Prefer the item's own id over its position when one exists -
            # "comments[id=1274].body" survives reordering and names the
            # actual Zendesk record; "comments[3].body" does not.
            if item.get("id") is not None:
                item_path = f"{path}[id={item['id']}]"
            result.append(_walk_dict(item, path=item_path))
        elif isinstance(item, list):
            result.append(_walk_list(item, path=item_path))
        elif isinstance(item, str):
            result.append(wrap(item, source=item_path))
        else:
            result.append(item)
    return result


def _walk_dict(node: dict[str, Any], *, path: str) -> dict[str, Any]:
    """Return a new dict with every requester-authored string wrapped.

    Recurses through nested dicts and lists without regard for which field
    names this module's author has heard of - see the module docstring for
    why an allowlist here was the defect, not a simplification.
    """
    result: dict[str, Any] = {}
    for key, value in node.items():
        child_path = f"{path}.{key}"
        if isinstance(value, dict):
            result[key] = _walk_dict(value, path=child_path)
        elif isinstance(value, list):
            result[key] = _walk_list(value, path=child_path)
        elif isinstance(value, str):
            result[key] = value if _is_machine_set(key) else wrap(value, source=child_path)
        else:
            # int, float, bool, None - never wrapped; there is no key check
            # that would apply to a non-string value in the first place.
            result[key] = value
    return result


def wrap_ticket(envelope: Envelope) -> Envelope:
    """Wrap every requester-authored string anywhere in a `{"ticket": {...}}` envelope."""
    ticket = envelope.get("ticket")
    ticket_id = ticket.get("id") if isinstance(ticket, dict) else None
    root = f"zendesk-ticket-{ticket_id}" if ticket_id is not None else "zendesk-ticket"
    return _walk_dict(envelope, path=root)


def wrap_comments(envelope: Envelope) -> Envelope:
    """Wrap every requester-authored string anywhere in a `{"comments": [...]}` envelope."""
    return _walk_dict(envelope, path="zendesk-comments")


def wrap_search(envelope: Envelope) -> Envelope:
    """Wrap every requester-authored string anywhere in a `{"results": [...]}` envelope.

    A search result is not always a ticket - an unconstrained query can return
    a user or an organization instead - so this walks generically rather than
    assuming ticket fields, the same way `wrap_ticket` and `wrap_comments` do.
    """
    return _walk_dict(envelope, path="zendesk-search")
