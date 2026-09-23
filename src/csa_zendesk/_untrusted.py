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

**Wrapping is not idempotent, and a genuine double-wrap now refuses rather than
silently mis-wrapping.** Calling `wrap()` (directly or via `wrap_*`) on text
that already has the exact SHAPE of one of its own envelopes - starts with
`MARKER_OPEN`, ends with `MARKER_CLOSE` - raises `ValueError` instead of
neutralising the first pass's own markers and enclosing the mess in a new
pair. That silent behaviour was the trap: non-idempotence that fails loudly is
a design choice, non-idempotence that fails silently into forged,
unauditable nested markers is not. The check is structural, not "does this
text contain a marker-shaped substring anywhere" - a hostile TICKET body that
embeds marker-shaped text mid-sentence (see `test_a_requester_cannot_escape_
the_block_by_writing_the_closing_marker` in `tests/test_untrusted.py`) does
not match that shape and is still neutralised and wrapped exactly as before;
only text that already IS a well-formed envelope from a prior `wrap()` call
is refused. Every value must be wrapped exactly once; Task 5 wraps a tool's
response as the last step before it leaves this library, never earlier - no
live path calls `wrap()` twice on the same value today, and this refusal
keeps it that way instead of leaving a silent trap for the day one is.

**`html_body` stops being HTML.** By the time this module ever sees it, `html_body`
has already been converted to Markdown at the `Backend` seam (`_markdown.to_markdown`,
Task 4 of the Block 2 plan) - so it stops being HTML there, not here. That is a
stronger and different reason than an earlier version of this docstring gave: it used
to credit `_neutralise` turning `<div>` into `‹div›` for the effect, which was true
back when `html_body` still arrived as raw HTML, but says nothing about the field's
representation today - `_neutralise` still runs over the (now Markdown) value like
every other string, but the format change happened upstream, one layer before this
module runs.

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
#:
#: **Known residue this denylist does not close (smaller item, final
#: whole-branch review): `metadata.custom`.** It holds arbitrary CLIENT-set
#: key/value pairs (API-SURFACE.md's comment metadata shape) - a caller of
#: the API, not this module, chooses those key NAMES, and one named
#: `status`/`type`/`url`/`role`/`public` (any member of this set) or ending
#: `_at` passes its VALUE through `_walk_dict` unwrapped, because the
#: machine-set test here is keyed on the field name alone with no way to tell
#: "Zendesk's own `status` field" from "a client's `metadata.custom.status`
#: string that merely reuses the name." No known live case has actually done
#: this; recorded so the gap is documented rather than discovered by an
#: attacker choosing that key on purpose.
_MACHINE_SET_KEYS = frozenset({"id", "url", "type", "status", "priority", "public", "result_type", "role"})


def _is_machine_set(key: str) -> bool:
    # `_at`-suffixed keys are Zendesk's uniform timestamp convention
    # (created_at, updated_at, due_at, solved_at, started_at, ...) - a fixed
    # naming pattern the vendor controls, never free text a requester wrote.
    return key in _MACHINE_SET_KEYS or key.endswith("_at")


def _neutralise(text: str) -> str:
    """Replace every `<`/`>` in `text` with a lookalike character."""
    return text.translate(_ANGLE_BRACKETS)


#: Keys expected to carry markup - `_walk_dict` passes `note_on_change=False`
#: for these (smaller item, final whole-branch review).
#:
#: `html_body` is Markdown by the time it reaches this module (converted at
#: the `Backend` seam, Task 4 of the Block 2 plan), not the raw HTML this
#: comment originally described - so the old justification ("contains a
#: literal `<` in EVERY comment that has one at all") stopped being true the
#: moment that conversion landed. The suppression is still correct, but for a
#: different reason: measured converting ten ordinary HTML shapes, 5 of 10
#: still contain `<` or `>` in their Markdown form. The decisive case is
#: `<blockquote>` -> `"> q"` - MARKDOWN'S OWN SYNTAX uses `>` for
#: blockquotes, and a quoted reply in an email chain (most support tickets)
#: produces exactly that. Code spans and escaped HTML entities preserve
#: literal `<` the same way. So `_neutralise` still changes `html_body` often
#: enough that the `(neutralised)` note firing on it would be noise exactly
#: where a real injection attempt would arrive, since a genuine escape
#: attempt reads identically to routine Markdown in the note - the same
#: suppression this comment always recommended, just no longer because
#: `html_body` is "basically always HTML".
#:
#: Chose suppression by key over trying to distinguish "contained angle
#: brackets" from "contained marker-shaped text": this module's whole design
#: is CHARACTER-level neutralisation specifically because substring/pattern
#: matching for marker-shaped text is a disguise an attacker can defeat
#: (module docstring, "Why character-level neutralisation, not substring
#: matching") - building a second, pattern-based detector just for the note
#: would reintroduce that exact class of bypass. A narrow, explicit,
#: single-purpose key allowlist is consistent with how `_MACHINE_SET_KEYS`
#: already carves out exceptions by key name, not by guessing content shape.
#: `plain_body` and `body` are NOT in this set: they are not expected to
#: carry markup, so a `<`/`>` in either is still worth flagging.
_MARKUP_KEYS = frozenset({"html_body"})


def wrap(text: str, *, source: str, note_on_change: bool = True) -> str:
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
    which would otherwise be indistinguishable from one. `note_on_change=False`
    (set by `_walk_dict` for keys in `_MARKUP_KEYS`, e.g. `html_body`) suppresses
    that note without suppressing neutralisation itself: a key EXPECTED to carry
    markup changes often enough in routine use (measured: 5 of 10 ordinary shapes,
    driven by Markdown's own `>` blockquote syntax) that letting the note fire
    on every occurrence would bury the signal exactly where an injection attempt
    would arrive - see `_MARKUP_KEYS`'s own comment for why this is a key-based
    allowlist rather than a second content-pattern detector.

    **Refuses to double-wrap.** Raises `ValueError` when `text` already has
    the exact shape of one of this function's own envelopes - starts with
    `MARKER_OPEN`, and (after trailing whitespace) ends with `MARKER_CLOSE`.
    This is a structural check on the well-formed envelope shape, not a bare
    substring search: hostile TICKET text containing a marker-shaped
    substring *embedded* in otherwise ordinary prose (the case
    `test_a_requester_cannot_escape_the_block_by_writing_the_closing_marker`
    and its siblings exercise) does not match this shape and is neutralised
    exactly as before - refusing on ANY marker-shaped substring would refuse
    to deliver ordinary hostile ticket content instead of framing it safely,
    which is the opposite of this module's job. What this refuses is the
    narrower, genuinely dangerous case: `wrap()` called a second time on its
    OWN prior output, which would otherwise neutralise the first pass's real
    markers into forged, ambiguous tamper-evidence and enclose the mess in a
    new pair - a silent failure. No live double-wrap path exists in this
    codebase today (every `wrap`/`wrap_*` call sits at the last step before a
    tool response leaves this library); this refusal is the backstop for the
    day an accidental one is introduced, so it fails loudly instead. Checked
    on `text` only, never `source`: every caller in this codebase builds
    `source` from machine ids and dotted field paths, so it never carries this
    shape in production.
    """
    if text.startswith(MARKER_OPEN) and text.rstrip().endswith(MARKER_CLOSE):
        raise ValueError(
            "wrap() was called on text that is already a wrapped envelope (it starts with "
            "MARKER_OPEN and ends with MARKER_CLOSE) - wrapping is not idempotent, and a second "
            "pass would neutralise the first pass's own markers into forged tamper-evidence "
            "rather than frame anything new. Wrap each value exactly once, at the last point "
            "before it leaves this library."
        )
    safe_text = _neutralise(text)
    safe_source = _neutralise(source).replace("\n", " ").replace("\r", " ")
    changed = safe_text != text or safe_source != source
    note = " (neutralised)" if changed and note_on_change else ""
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
            result[key] = (
                value
                if _is_machine_set(key)
                else wrap(value, source=child_path, note_on_change=key not in _MARKUP_KEYS)
            )
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


def wrap_upload(envelope: Envelope) -> Envelope:
    """Wrap an `{"upload": {...}}` envelope - `upload_file` and `delete_upload`.

    A sibling rather than a reuse of `wrap_ticket`, for the same reason
    `wrap_comments` and `wrap_search` are siblings: the function name IS the
    provenance root, so reusing `wrap_ticket` here would not save a sibling,
    it would label an upload response `source=zendesk-ticket...` and state
    something false about where the bytes came from. The markers would still
    delimit the data correctly; the claim beside them would be wrong, and a
    marker whose provenance cannot be trusted is worth less than one whose can.
    """
    return _walk_dict(envelope, path="zendesk-upload")


def wrap_attachment(envelope: Envelope) -> Envelope:
    """Wrap an `{"attachment": {...}}` envelope - `get_attachment`.

    `wrap_upload`'s sibling, for the reason given there. An attachment carries
    a requester-authored `file_name` and `content_url`, so this is not a
    formality.
    """
    return _walk_dict(envelope, path="zendesk-attachment")


def wrap_search(envelope: Envelope) -> Envelope:
    """Wrap every requester-authored string anywhere in a `{"results": [...]}` envelope.

    `backend.ApiBackend.search_tickets` now composes `type:ticket` onto every
    query (Important 5, final whole-branch review), so this tool's own results
    should never carry a user or organization object again - but this function
    stays generic rather than assuming ticket-only fields, the same way
    `wrap_ticket` and `wrap_comments` do: it is a defence-in-depth layer, not
    the constraint itself, and a caller of the library (or a future tool) that
    reaches `search_tickets` on an unconstrained backend, or a Zendesk change
    to what `type:ticket` matches, must not silently reach a model unwrapped.
    `test_wrap_search_wraps_a_user_result_from_an_unconstrained_query` in
    `tests/test_untrusted.py` keeps proving this generic behaviour holds, even
    though the constraint above means the tool itself should never exercise it
    in practice.
    """
    return _walk_dict(envelope, path="zendesk-search")
