"""Pagination, which Zendesk gets wrong in three ways that all return HTTP 200.

1. A request carrying BOTH a cursor page parameter and the older offset/sort
   parameters returns 200 and silently discards the sort - byte-identical to
   sending no sort at all. Zendesk's own PHP client strips the offset params
   before every cursor request, so the hazard is known internally. We refuse
   instead of stripping, because silently changing what the caller asked for
   is the same class of defect as the one we are refusing.

2. Cursor paging honours only three sort fields. `created_at` is not among
   them, so "the newest tickets" - the most natural request anyone makes - has
   no direct expression. `-id` is the proxy, since ids ascend with creation.
   `translate_sort` makes that substitution in one place so no caller has to
   know it.

3. The style is a property of the RESPONSE, not of the request: some endpoints
   answer cursor-shaped whether or not you asked. Detect, never assume - and
   never read silence (no `meta`, no `links`) as "unpaginated".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .exceptions import PaginationError

#: Sort fields cursor pagination will honour. Everything else returns HTTP 400
#: InvalidPaginationParameter. Probe-verified against tickets.
CURSOR_SORTABLE: frozenset[str] = frozenset({"updated_at", "id", "status"})

#: Offset-style query keys.
OFFSET_KEYS: frozenset[str] = frozenset({"page", "per_page", "sort_by", "sort_order"})

#: Cursor-style query keys.
CURSOR_KEYS: frozenset[str] = frozenset({"page[size]", "page[after]", "page[before]"})


def check_params(params: Mapping[str, Any]) -> None:
    """Refuse a query that Zendesk would accept and answer wrongly.

    Raises `PaginationError` naming the conflicting keys and the remedy;
    returns `None` when the query is safe to send as-is.
    """
    present = {k for k, v in params.items() if v is not None}
    cursor_present = present & CURSOR_KEYS
    offset_present = present & OFFSET_KEYS
    if cursor_present and offset_present:
        raise PaginationError(
            f"refusing to send both pagination styles on one request: cursor params "
            f"{sorted(cursor_present)} together with offset params {sorted(offset_present)}. "
            f"Zendesk accepts this and answers HTTP 200, but silently discards sort_by/"
            f"sort_order and serves default order - indistinguishable from a request with "
            f"no sort at all. Send {sorted(cursor_present)} with `sort` alone (only "
            f"{sorted(CURSOR_SORTABLE)} are honoured), or drop {sorted(cursor_present)} and "
            f"use per_page/page with sort_by/sort_order instead - never both."
        )
    sort = params.get("sort")
    if cursor_present and sort is not None:
        field = str(sort).lstrip("-")
        if field not in CURSOR_SORTABLE:
            raise PaginationError(
                f"cursor pagination cannot sort by {field!r}; Zendesk answers this with "
                f"HTTP 400 InvalidPaginationParameter. It honours only "
                f"{sorted(CURSOR_SORTABLE)}. For 'newest first', use sort='-id' (or "
                f"translate_sort('-created_at')) since created_at is not cursor-sortable "
                f"but ids ascend with creation."
            )


def is_cursor_response(body: Mapping[str, Any]) -> bool:
    """Whether a response is cursor-paginated, judged by its own shape.

    Zendesk's official Ruby client uses exactly this test - `meta` AND `links`
    both present as objects - because the request does not determine the
    answer: some endpoints respond cursor-shaped unconditionally.
    """
    return isinstance(body.get("meta"), Mapping) and isinstance(body.get("links"), Mapping)


def next_cursor(body: Mapping[str, Any]) -> str | None:
    """The cursor for the next page, or `None` when the collection is exhausted.

    `has_more` is authoritative: `after_cursor` can still be populated on the
    last page, and following it there returns an empty page forever.
    """
    if not is_cursor_response(body):
        return None
    meta = body["meta"]
    if not meta.get("has_more"):
        return None
    after = meta.get("after_cursor")
    return str(after) if after else None


def translate_sort(sort: str) -> str:
    """Map a caller's sort intent onto something cursor paging can serve.

    `created_at` becomes `id`: not an alias, a proxy that happens to be exact,
    because Zendesk ids ascend with creation. Doing the substitution here means
    no caller has to know cursor paging cannot honour `created_at` directly.
    """
    descending = sort.startswith("-")
    field = sort.lstrip("-")
    if field == "created_at":
        field = "id"
    return f"-{field}" if descending else field
