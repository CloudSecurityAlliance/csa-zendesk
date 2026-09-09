# Block 0 — Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the whole vertical — HTTP, error taxonomy, pagination, the `Backend` seam, the fail-closed policy wrapper, and the client — on a single method, `get_ticket`, with CI gating it.

**Architecture:** A `Backend` protocol returns raw upstream envelopes. `PolicyBackend` wraps it and refuses any method without a declared capability gate. `ZendeskClient` is a thin typed surface over the wrapped backend. Nothing in this block touches MCP; that starts in Block 1.

**Tech Stack:** Python ≥3.10 · `httpx` · `pytest` · `ruff` · `mypy --strict` · GitHub Actions

**Spec:** `docs/superpowers/specs/2026-09-01-csa-zendesk-design.md` (revision 2)

## Global Constraints

- **Python floor 3.10.** `TypedDict` must come from `typing_extensions`, not `typing` — below 3.12 pydantic silently emits no schema from `typing.TypedDict`.
- **`httpx`, not `requests`** — one client type for sync now and async later.
- **`ruff`** with `select = ["E","F","W","I","B","UP"]`, line length 120, and `E702` **deliberately ignored** (one-line `a = ...; b = ...` is house style in the sibling repos).
- **`mypy`** runs with `strict = true` over `src` only.
- **Nothing may write to stdout** anywhere in the package. Under stdio MCP, stdout *is* the JSON-RPC channel. Diagnostics go to `logging`.
- **Never interpolate a credential** into a message, a log line, or a `__repr__`.
- **`ZENDESK_SUBDOMAIN` has no default.** A hardcoded tenant is both a leak and a footgun.
- **Keyword-only arguments on every `Backend` method**, so `PolicyBackend` can wrap uniformly.
- **The `Backend` returns raw envelopes.** No shaping, no models, no attribute access — `dict` in, `dict` out.
- Every commit message uses a conventional prefix (`feat:`, `fix:`, `test:`, `chore:`, `ci:`).
- **Branch and PR** — never commit to `main`. `scripts/check_public_safe.py` must pass before every push; the pre-commit hook runs it.

---

### Task 1: Package skeleton and CI

**Files:**
- Create: `src/csa_zendesk/__init__.py`
- Create: `tests/__init__.py`, `tests/test_public_api.py`
- Create: `.github/workflows/tests.yml`
- Modify: `pyproject.toml` (already exists; add nothing — verify it resolves)

**Interfaces:**
- Consumes: nothing.
- Produces: `csa_zendesk.__version__: str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_public_api.py
import csa_zendesk


def test_version_is_a_dotted_string():
    assert isinstance(csa_zendesk.__version__, str)
    assert csa_zendesk.__version__.count(".") >= 2


def test_package_exports_nothing_it_has_not_declared():
    # __all__ is the contract. Anything public and undeclared is an accident.
    public = {n for n in dir(csa_zendesk) if not n.startswith("_")}
    assert public == set(csa_zendesk.__all__)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_public_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'csa_zendesk'`

- [ ] **Step 3: Create the package**

```python
# src/csa_zendesk/__init__.py
"""A Python library and local stdio MCP server over the Zendesk REST API.

Nothing here is implemented beyond Block 0's vertical slice. See
docs/superpowers/specs/ for the design.
"""
from __future__ import annotations

__version__ = "0.0.1"

__all__ = ["__version__"]
```

```python
# tests/__init__.py  (empty file — makes tests a package so relative helpers work)
```

- [ ] **Step 4: Install and run**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -q -e ".[dev]"
.venv/bin/python -m pytest tests/test_public_api.py -v
```
Expected: 2 passed

- [ ] **Step 5: Add CI**

```yaml
# .github/workflows/tests.yml
name: tests
on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
      - uses: actions/setup-python@0b93645e9fea7318ecaed2b359559ac225c90a2b  # v5.3.0
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]"
      - run: ruff check src tests scripts
      - run: mypy

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
      - uses: actions/setup-python@0b93645e9fea7318ecaed2b359559ac225c90a2b  # v5.3.0
        with: { python-version: "${{ matrix.python-version }}" }
      - run: pip install -e ".[dev]"
      - run: python -m pytest -q --cov=csa_zendesk --cov-report=term-missing --cov-fail-under=90

  gates:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
      - uses: actions/setup-python@0b93645e9fea7318ecaed2b359559ac225c90a2b  # v5.3.0
        with: { python-version: "3.12" }
      - run: pip install pyyaml
      # The publication gate runs with STRUCTURAL patterns only in CI - the tenant
      # term list is gitignored and absent here. It reports reduced coverage rather
      # than a false pass, which is the intended behaviour.
      - run: python3 scripts/check_public_safe.py
      - run: python3 scripts/check_spec.py
```

- [ ] **Step 6: Verify lint and types pass on the skeleton**

Run: `.venv/bin/ruff check src tests scripts && .venv/bin/mypy`
Expected: no errors. If `mypy` complains about `scripts/`, confirm `pyproject.toml` has `files = ["src"]`.

- [ ] **Step 7: Commit**

```bash
git add src tests .github pyproject.toml
git commit -m "feat: package skeleton and CI gates"
```

---

### Task 2: The typed error hierarchy

**Files:**
- Create: `src/csa_zendesk/exceptions.py`
- Create: `tests/test_exceptions.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ZendeskError`, `CredentialsRejected`, `PlanBoundary`, `EndpointNotAvailable`, `NotFound`, `ValidationError(problems: dict[str, list[dict[str, Any]]])`, `PaginationError`, `SearchLimitExceeded`, `RateLimited(retry_after: int)`, `ServiceUnavailable(retry_after: int)`, `PolicyError`, `ApiError(status: int)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exceptions.py
import pytest

from csa_zendesk import exceptions as exc


def test_every_error_descends_from_the_base():
    for name in exc.__all__:
        cls = getattr(exc, name)
        assert issubclass(cls, exc.ZendeskError), name


def test_validation_error_carries_problems_keyed_by_field():
    # details is a MAP from field name to problems - `base` for whole-record
    # issues, the field name for field-scoped ones. Never index one key.
    e = exc.ValidationError("refused", problems={
        "base": [{"description": "Assignee: is required when solving a ticket"}],
        "status": [{"description": "closed prevents ticket update"}],
    })
    assert set(e.problems) == {"base", "status"}
    assert "Assignee" in str(e)


def test_rate_limited_carries_retry_after():
    assert exc.RateLimited("slow down", retry_after=42).retry_after == 42


def test_plan_boundary_is_not_confused_with_an_outage():
    assert not issubclass(exc.PlanBoundary, exc.ServiceUnavailable)
    assert not issubclass(exc.ServiceUnavailable, exc.PlanBoundary)


def test_credentials_are_never_interpolated_into_a_message():
    # Guard against the whole class of leak: no error takes a credential.
    import inspect
    for name in exc.__all__:
        sig = inspect.signature(getattr(exc, name))
        for p in sig.parameters:
            assert p not in ("token", "password", "secret", "api_token"), f"{name}.{p}"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_exceptions.py -v`
Expected: FAIL — `ImportError: cannot import name 'exceptions'`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/exceptions.py
"""Typed errors. One translation layer at each delivery boundary turns these into
whatever that boundary needs (an MCP `ToolError`, an HTTP status, a log line).

Never interpolate a credential into a message: embedders log these objects.

The hierarchy is deliberately finer than the official Zendesk clients', which
collapse everything outside 404/422 into a network error - so a 403 plan boundary
reads as an outage and the caller cannot tell "your plan" from "we are down".
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "ZendeskError", "CredentialsRejected", "PlanBoundary", "EndpointNotAvailable",
    "NotFound", "ValidationError", "PaginationError", "SearchLimitExceeded",
    "RateLimited", "ServiceUnavailable", "PolicyError", "ApiError",
]


class ZendeskError(Exception):
    """Base for everything this package raises."""


class CredentialsRejected(ZendeskError):
    """The credential is absent, wrong, or expired. Not a scope problem."""


class PlanBoundary(ZendeskError):
    """HTTP 403: authenticated and refused. A plan or feature boundary.

    NOT an outage, and not a fault in the request. The official clients get this
    wrong by mapping it to a generic network error.
    """


class EndpointNotAvailable(ZendeskError):
    """HTTP 404 `InvalidEndpoint` - the route is not on this account."""


class NotFound(ZendeskError):
    """HTTP 404 `RecordNotFound` - the route exists, the record does not."""


class ValidationError(ZendeskError):
    """HTTP 422 `RecordInvalid`, carrying the parsed `details`.

    `problems` is a MAP from field name to a list of problems. Whole-record
    problems arrive under `base`; field-scoped ones under the field's own name
    (`details.status[]`). Iterate the keys; never index one.
    """

    def __init__(self, message: str, *, problems: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.problems: dict[str, list[dict[str, Any]]] = problems or {}
        detail = "; ".join(
            f"{field}: " + ", ".join(str(p.get("description", p)) for p in items)
            for field, items in self.problems.items()
        )
        super().__init__(f"{message} - {detail}" if detail else message)


class PaginationError(ZendeskError):
    """A pagination request that cannot be served, or must not be sent.

    Covers Zendesk's `InvalidPaginationDepth` (offset past 10,000 records) and
    `InvalidPaginationParameter` (a sort field the chosen style cannot honour),
    and our own refusal to emit two pagination styles on one request.
    """


class SearchLimitExceeded(ZendeskError):
    """Search refuses past 1000 results, however large the reported `count`."""


class RateLimited(ZendeskError):
    """HTTP 429. Always retryable."""

    def __init__(self, message: str, *, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class ServiceUnavailable(ZendeskError):
    """HTTP 503, typically maintenance. Retryable for idempotent requests only."""

    def __init__(self, message: str, *, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class PolicyError(ZendeskError):
    """The local capability policy refused this call. Not an upstream failure.

    The message names the missing capability and what an operator changes to
    grant it - the refusal is the product, not an error path.
    """


class ApiError(ZendeskError):
    """An upstream failure that is none of the typed cases above."""

    def __init__(self, message: str, *, status: int = 0) -> None:
        self.status = status
        super().__init__(message)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_exceptions.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/exceptions.py tests/test_exceptions.py
git commit -m "feat: typed error hierarchy, finer than the official clients'"
```

---

### Task 3: The error parser — four envelope shapes

**Files:**
- Create: `src/csa_zendesk/_errors.py`
- Create: `tests/test_errors.py`

**Interfaces:**
- Consumes: `csa_zendesk.exceptions` (all types from Task 2).
- Produces: `parse_error(status: int, body: object, *, headers: Mapping[str, str] | None = None) -> ZendeskError` and `extract_problems(body: dict[str, Any]) -> dict[str, list[dict[str, Any]]]`.

- [ ] **Step 1: Write the failing test**

Every body below is a **verbatim capture from the live API**, recorded in `analysis/API-SURFACE.md`. Do not paraphrase them.

```python
# tests/test_errors.py
import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk._errors import extract_problems, parse_error


def test_shape_1_error_as_an_object():
    e = parse_error(403, {"error": {"title": "Forbidden",
                                    "message": "You do not have access to this page."}})
    assert isinstance(e, exc.PlanBoundary)


def test_shape_2_error_as_a_string_with_a_description():
    assert isinstance(parse_error(404, {"error": "RecordNotFound",
                                        "description": "Not found"}), exc.NotFound)
    assert isinstance(parse_error(404, {"error": "InvalidEndpoint",
                                        "description": "Not found"}), exc.EndpointNotAvailable)


def test_shape_3_errors_as_an_array():
    e = parse_error(400, {"errors": [{"code": "InvalidPaginationDepth",
                                      "title": "Pagination requests using Offset Pagination are limited"}]})
    assert isinstance(e, exc.PaginationError)


def test_shape_4_string_plus_description_plus_details():
    e = parse_error(422, {
        "error": "RecordInvalid", "description": "Record validation errors",
        "details": {"base": [
            {"description": "Assignee: is required when solving a ticket",
             "ticket_field_id": 1, "ticket_field_type": "FieldAssignee"}]}})
    assert isinstance(e, exc.ValidationError)
    assert "Assignee" in str(e)


def test_details_keyed_by_field_not_only_base():
    # The closed-ticket refusal keys details by the FIELD name. A parser that
    # reads details.base finds nothing here.
    e = parse_error(422, {"error": "RecordInvalid", "description": "Record validation errors",
                          "details": {"status": [{"description": "closed prevents ticket update"}]}})
    assert isinstance(e, exc.ValidationError)
    assert set(e.problems) == {"status"}
    assert "closed prevents ticket update" in str(e)


def test_a_parser_reading_only_error_and_description_would_lose_the_diagnosis():
    body = {"error": "RecordInvalid", "description": "Record validation errors",
            "details": {"base": [{"description": "the actual reason"}]}}
    assert "the actual reason" in str(parse_error(422, body))


def test_bare_string_error():
    assert isinstance(parse_error(401, {"error": "Couldn't authenticate you"}),
                      exc.CredentialsRejected)


def test_search_response_limit():
    e = parse_error(422, {"error": "invalid",
                          "description": "Invalid search: Requested response size was greater "
                                         "than Search Response Limits"})
    assert isinstance(e, exc.SearchLimitExceeded)


def test_rate_limited_reads_retry_after():
    e = parse_error(429, {}, headers={"Retry-After": "17"})
    assert isinstance(e, exc.RateLimited) and e.retry_after == 17


def test_rate_limited_defaults_when_the_header_is_absent():
    # 10s is the official Ruby client's DEFAULT_RETRY_AFTER.
    e = parse_error(429, {}, headers={})
    assert isinstance(e, exc.RateLimited) and e.retry_after == 10


def test_503_is_service_unavailable_and_carries_retry_after():
    e = parse_error(503, {}, headers={"Retry-After": "30"})
    assert isinstance(e, exc.ServiceUnavailable) and e.retry_after == 30


def test_a_body_that_is_not_a_dict_is_still_an_error_not_a_crash():
    assert isinstance(parse_error(500, "<html>gateway</html>"), exc.ApiError)
    assert isinstance(parse_error(500, None), exc.ApiError)


def test_unknown_status_becomes_apierror_carrying_the_status():
    e = parse_error(418, {"error": "teapot"})
    assert isinstance(e, exc.ApiError) and e.status == 418


def test_extract_problems_ignores_a_details_that_is_not_a_map():
    assert extract_problems({"details": "nope"}) == {}
    assert extract_problems({"details": {"base": "not a list"}}) == {}
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'csa_zendesk._errors'`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/_errors.py
"""Translate a Zendesk error response into a typed exception.

Zendesk returns errors in FOUR mutually incompatible envelopes, all observed live:

  {"error": {"title": ..., "message": ...}}                       object
  {"error": "RecordNotFound", "description": "Not found"}          string + description
  {"errors": [{"code": ..., "title": ..., "detail": ...}]}         array
  {"error": "RecordInvalid", "description": ..., "details": {...}} the above plus details

Zendesk's own Ruby client reads `details` first and then falls back across four
keys, because it cannot predict the shape either.

`details` is a MAP from field name to a list of problems. Whole-record problems
arrive under `base`; field-scoped ones under the field's own name. A parser
hardcoded to `details.base` silently finds nothing on a field-scoped error.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import exceptions as exc

DEFAULT_RETRY_AFTER = 10


def extract_problems(body: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Pull `details` out as a field -> problems map, defensively.

    Anything that is not a map of lists-of-maps is discarded rather than trusted:
    a malformed `details` must not become a crash inside error handling.
    """
    details = body.get("details")
    if not isinstance(details, Mapping):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for field, items in details.items():
        if isinstance(items, list) and all(isinstance(i, Mapping) for i in items):
            out[str(field)] = [dict(i) for i in items]
    return out


def _message(body: Mapping[str, Any]) -> str:
    """Best available human text, trying every key Zendesk uses."""
    err = body.get("error")
    if isinstance(err, Mapping):
        parts = [str(err.get(k, "")) for k in ("title", "message")]
        return " - ".join(p for p in parts if p) or "error"
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
        first = errors[0]
        parts = [str(first.get(k, "")) for k in ("code", "title", "detail")]
        return " - ".join(p for p in parts if p) or "error"
    parts = [str(body.get(k, "")) for k in ("error", "description", "message")]
    return " - ".join(p for p in parts if p) or "error"


def _code(body: Mapping[str, Any]) -> str:
    """The machine-readable code, wherever this envelope keeps it."""
    err = body.get("error")
    if isinstance(err, str):
        return err
    if isinstance(err, Mapping):
        return str(err.get("title", ""))
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
        return str(errors[0].get("code", ""))
    return ""


def _retry_after(headers: Mapping[str, str] | None) -> int:
    if not headers:
        return DEFAULT_RETRY_AFTER
    raw = headers.get("Retry-After") or headers.get("retry-after")
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER


def parse_error(status: int, body: object,
                *, headers: Mapping[str, str] | None = None) -> exc.ZendeskError:
    """Map a failed response to a typed exception. Never raises."""
    if status == 429:
        return exc.RateLimited("Zendesk rate limit reached", retry_after=_retry_after(headers))
    if status == 503:
        return exc.ServiceUnavailable("Zendesk is unavailable, likely maintenance",
                                      retry_after=_retry_after(headers))

    # ZD-2: a body that is not a JSON object is an error, not something to hand on.
    if not isinstance(body, Mapping):
        return exc.ApiError(f"Zendesk returned HTTP {status} with a body that is not "
                            f"a JSON object", status=status)

    code, message = _code(body), _message(body)

    if status == 401:
        return exc.CredentialsRejected(
            f"Zendesk rejected the credential ({message}). Check CINO_CSA_ZENDESK and "
            f"CINO_CSA_ZENDESK_EMAIL, or re-run authorisation.")
    if status == 403:
        return exc.PlanBoundary(
            f"Zendesk refused this endpoint for this account ({message}). This is a plan "
            f"or feature boundary, not an outage and not a bad request.")
    if status == 404:
        if code == "InvalidEndpoint":
            return exc.EndpointNotAvailable(
                f"this endpoint is not available on this account ({message})")
        return exc.NotFound(f"no such record ({message})")
    if status == 422:
        if "Search Response Limit" in message:
            return exc.SearchLimitExceeded(
                "search refuses past 1000 results, however large the reported count; "
                "use the export path for more")
        problems = extract_problems(body)
        if code == "RecordInvalid" or problems:
            return exc.ValidationError("Zendesk refused the record", problems=problems)
        return exc.ApiError(f"Zendesk rejected the request: {message}", status=status)
    if status == 400 and code in ("InvalidPaginationDepth", "InvalidPaginationParameter"):
        return exc.PaginationError(f"{code}: {message}")

    return exc.ApiError(f"Zendesk returned HTTP {status}: {message}", status=status)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_errors.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/_errors.py tests/test_errors.py
git commit -m "feat: error parser covering all four Zendesk envelope shapes"
```

---

### Task 4: Pagination — style detection and the both-styles refusal

**Files:**
- Create: `src/csa_zendesk/_pagination.py`
- Create: `tests/test_pagination.py`

**Interfaces:**
- Consumes: `csa_zendesk.exceptions.PaginationError`.
- Produces: `CURSOR_SORTABLE: frozenset[str]`, `OFFSET_KEYS: frozenset[str]`, `CURSOR_KEYS: frozenset[str]`, `check_params(params: Mapping[str, Any]) -> None`, `is_cursor_response(body: Mapping[str, Any]) -> bool`, `next_cursor(body: Mapping[str, Any]) -> str | None`, `translate_sort(sort: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pagination.py
import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk._pagination import (
    CURSOR_SORTABLE, check_params, is_cursor_response, next_cursor, translate_sort,
)


def test_cursor_params_alone_are_fine():
    check_params({"page[size]": 100, "sort": "-updated_at"})


def test_offset_params_alone_are_fine():
    check_params({"per_page": 100, "sort_by": "created_at", "sort_order": "desc"})


def test_mixing_the_two_styles_is_refused():
    # Live behaviour: Zendesk returns 200 and SILENTLY DISCARDS the sort, giving
    # default ordering. Byte-identical to sending no sort at all. We refuse.
    with pytest.raises(exc.PaginationError, match="both pagination styles"):
        check_params({"page[size]": 3, "sort_by": "updated_at", "sort_order": "desc"})
    with pytest.raises(exc.PaginationError):
        check_params({"page[after]": "abc", "per_page": 10})


def test_a_sort_field_cursor_paging_cannot_honour_is_refused_locally():
    # Live: sort=created_at with page[size] -> 400 InvalidPaginationParameter.
    # Refusing locally saves a round trip and gives a better message.
    with pytest.raises(exc.PaginationError, match="created_at"):
        check_params({"page[size]": 10, "sort": "created_at"})
    with pytest.raises(exc.PaginationError):
        check_params({"page[size]": 10, "sort": "-assignee.name"})


def test_the_three_cursor_sortable_fields_are_accepted_in_both_directions():
    assert CURSOR_SORTABLE == frozenset({"updated_at", "id", "status"})
    for field in CURSOR_SORTABLE:
        check_params({"page[size]": 10, "sort": field})
        check_params({"page[size]": 10, "sort": f"-{field}"})


def test_style_is_detected_from_the_response_not_the_request():
    # Some endpoints answer cursor-shaped whether or not you asked.
    assert is_cursor_response({"tickets": [], "meta": {"has_more": False}, "links": {}})
    assert not is_cursor_response({"tickets": [], "next_page": None, "count": 0})
    assert not is_cursor_response({"meta": {"has_more": True}})   # meta without links


def test_next_cursor_is_none_when_there_is_no_more():
    assert next_cursor({"meta": {"has_more": False, "after_cursor": "x"}, "links": {}}) is None
    assert next_cursor({"meta": {"has_more": True, "after_cursor": "abc"}, "links": {}}) == "abc"
    assert next_cursor({"count": 3, "next_page": None}) is None


def test_newest_first_translates_to_id_because_created_at_is_not_cursor_sortable():
    assert translate_sort("-created_at") == "-id"
    assert translate_sort("created_at") == "id"
    assert translate_sort("-updated_at") == "-updated_at"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_pagination.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'csa_zendesk._pagination'`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/_pagination.py
"""Pagination, which Zendesk gets wrong in three ways that all return HTTP 200.

1. A request carrying BOTH a cursor page parameter and the older sort parameters
   returns 200 and silently discards the sort - byte-identical to sending no sort.
   Zendesk's own PHP client strips the offset params before every cursor request,
   so the hazard is known internally. We refuse instead of stripping, because
   silently changing what the caller asked for is the same class of defect.

2. Cursor paging honours only three sort fields. `created_at` is not among them,
   so "the newest tickets" - the most natural request anyone makes - has no direct
   expression. `-id` is the proxy, since ids ascend with creation.

3. The style is a property of the RESPONSE, not of the request: some endpoints
   answer cursor-shaped whether or not you asked. Detect, never assume.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .exceptions import PaginationError

#: Sort fields cursor pagination will honour. Everything else returns
#: 400 InvalidPaginationParameter. Probe-verified against tickets.
CURSOR_SORTABLE = frozenset({"updated_at", "id", "status"})

#: Offset-style query keys.
OFFSET_KEYS = frozenset({"page", "per_page", "sort_by", "sort_order"})

#: Cursor-style query keys.
CURSOR_KEYS = frozenset({"page[size]", "page[after]", "page[before]"})


def check_params(params: Mapping[str, Any]) -> None:
    """Refuse a query that Zendesk would accept and answer wrongly.

    Raises PaginationError; returns None when the query is safe to send.
    """
    present = {k for k, v in params.items() if v is not None}
    cursor = present & CURSOR_KEYS
    offset = present & OFFSET_KEYS
    if cursor and offset:
        raise PaginationError(
            f"refusing to send both pagination styles on one request: "
            f"{sorted(cursor)} with {sorted(offset)}. Zendesk answers 200 and silently "
            f"discards the sort, so the result would be plausible and wrongly ordered. "
            f"Use page[size]/page[after] with `sort`, or per_page/page with "
            f"sort_by/sort_order - never both.")
    if cursor:
        sort = params.get("sort")
        if sort is not None:
            field = str(sort).lstrip("-")
            if field not in CURSOR_SORTABLE:
                raise PaginationError(
                    f"cursor pagination cannot sort by {field!r}; it honours only "
                    f"{sorted(CURSOR_SORTABLE)}. For 'newest first' use sort=-id, since "
                    f"created_at is not cursor-sortable and ids ascend with creation.")


def is_cursor_response(body: Mapping[str, Any]) -> bool:
    """Whether a response is cursor-paginated, judged by its own shape.

    Zendesk's official Ruby client uses exactly this test - `meta` AND `links` -
    because the request does not determine the answer.
    """
    return isinstance(body.get("meta"), Mapping) and isinstance(body.get("links"), Mapping)


def next_cursor(body: Mapping[str, Any]) -> str | None:
    """The cursor for the next page, or None when the collection is exhausted.

    `has_more` is authoritative: `after_cursor` is populated on the last page too,
    and following it returns an empty page forever.
    """
    if not is_cursor_response(body):
        return None
    meta = body["meta"]
    if not meta.get("has_more"):
        return None
    after = meta.get("after_cursor")
    return str(after) if after else None


def translate_sort(sort: str) -> str:
    """Map a caller's intent onto something cursor paging can serve.

    `created_at` becomes `id`: not an alias, a proxy that happens to be exact,
    because Zendesk ids ascend with creation. Doing this in one place means no
    caller has to know it.
    """
    descending = sort.startswith("-")
    field = sort.lstrip("-")
    if field == "created_at":
        field = "id"
    return f"-{field}" if descending else field
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_pagination.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/_pagination.py tests/test_pagination.py
git commit -m "feat: pagination style detection and the both-styles refusal"
```

---

### Task 5: The HTTP client — auth, retry, and error translation

**Files:**
- Create: `src/csa_zendesk/_http.py`
- Create: `tests/test_http.py`

**Interfaces:**
- Consumes: `_errors.parse_error`, `_pagination.check_params`, `exceptions.*`.
- Produces: `HttpClient(subdomain: str, email: str, api_token: str, transport: httpx.BaseTransport | None = None, timeout: float = 30.0)` with `.get(path, *, params=None) -> dict[str, Any]` and `.request(method, path, *, params=None, json=None, idempotent=True) -> dict[str, Any]`; module constant `MAX_RETRIES: int`.

`transport` exists so tests drive `httpx.MockTransport` and never touch the network. This is the `ApiBackend`-only behaviour the spec's §7 says needs stub-service tests rather than `FakeBackend` tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http.py
import httpx
import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk._http import HttpClient


def client(handler, **kw):
    return HttpClient(subdomain="example", email="agent@example.com", api_token="tok",
                      transport=httpx.MockTransport(handler), **kw)


def test_a_get_returns_the_parsed_envelope_unshaped():
    def handler(request):
        assert request.url.path == "/api/v2/tickets/1.json"
        return httpx.Response(200, json={"ticket": {"id": 1, "subject": "hi"}})
    assert client(handler).get("/api/v2/tickets/1.json") == {"ticket": {"id": 1, "subject": "hi"}}


def test_it_sends_api_token_basic_auth_in_the_email_slash_token_form():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})
    client(handler).get("/api/v2/tickets.json")
    import base64
    expected = base64.b64encode(b"agent@example.com/token:tok").decode()
    assert seen["auth"] == f"Basic {expected}"


def test_no_credential_ever_appears_in_an_exception_message():
    def handler(request):
        return httpx.Response(401, json={"error": "Couldn't authenticate you"})
    with pytest.raises(exc.CredentialsRejected) as ei:
        client(handler).get("/api/v2/tickets.json")
    assert "tok" not in str(ei.value)


def test_a_200_that_is_not_json_is_an_error_not_a_return_value():
    def handler(request):
        return httpx.Response(200, text="<html>hello</html>")
    with pytest.raises(exc.ApiError, match="not JSON"):
        client(handler).get("/api/v2/tickets.json")


def test_a_200_whose_json_is_not_an_object_is_an_error():
    def handler(request):
        return httpx.Response(200, json=[1, 2, 3])
    with pytest.raises(exc.ApiError, match="not a JSON object"):
        client(handler).get("/api/v2/tickets.json")


def test_429_is_retried_honouring_retry_after_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})
    assert client(handler).get("/api/v2/tickets.json") == {"ok": True}
    assert calls["n"] == 2


def test_503_is_retried_for_an_idempotent_request():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={}, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})
    assert client(handler).request("GET", "/api/v2/tickets.json") == {"ok": True}


def test_503_is_NOT_retried_for_a_non_idempotent_write():
    # The mutation may already have landed. Retrying could double-apply it.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={}, headers={"Retry-After": "0"})
    with pytest.raises(exc.ServiceUnavailable):
        client(handler).request("POST", "/api/v2/tickets.json", json={}, idempotent=False)
    assert calls["n"] == 1


def test_429_IS_retried_even_for_a_non_idempotent_write():
    # A rate limit means the request was refused, not applied.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"ok": True})
    assert client(handler).request("POST", "/api/v2/tickets.json", json={},
                                   idempotent=False) == {"ok": True}
    assert calls["n"] == 2


def test_retries_are_bounded():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={}, headers={"Retry-After": "0"})
    with pytest.raises(exc.RateLimited):
        client(handler).get("/api/v2/tickets.json")
    assert calls["n"] <= 1 + 3


def test_mixed_pagination_params_are_refused_before_any_request_is_made():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={})
    with pytest.raises(exc.PaginationError):
        client(handler).get("/api/v2/tickets.json",
                            params={"page[size]": 5, "sort_by": "updated_at"})
    assert calls["n"] == 0


def test_none_valued_params_are_dropped_rather_than_sent_as_none():
    def handler(request):
        assert "sort" not in request.url.params
        assert request.url.params.get("page[size]") == "5"
        return httpx.Response(200, json={})
    client(handler).get("/api/v2/tickets.json", params={"page[size]": 5, "sort": None})


def test_a_transport_level_failure_becomes_a_typed_error():
    def handler(request):
        raise httpx.ConnectError("no route to host")
    with pytest.raises(exc.ApiError, match="could not reach Zendesk"):
        client(handler).get("/api/v2/tickets.json")


def test_subdomain_is_required():
    with pytest.raises(ValueError, match="ZENDESK_SUBDOMAIN"):
        HttpClient(subdomain="", email="agent@example.com", api_token="tok")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_http.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'csa_zendesk._http'`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/_http.py
"""HTTP transport: auth, retries, and the rule that a wrong-looking 200 is an error.

Deliberately small. ADR-002 records that if this file passes roughly 400 lines we
are writing a client library by accident and should say so.

The retry rules are not symmetric, and the asymmetry is the point:

  429  always retryable - the request was REFUSED, so nothing was applied.
  503  retryable for idempotent requests only. For a non-idempotent write the
       mutation may already have landed, and retrying could double-apply it.
"""
from __future__ import annotations

import base64
import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from . import exceptions as exc
from ._errors import parse_error
from ._pagination import check_params

log = logging.getLogger(__name__)

MAX_RETRIES = 3
_NON_IDEMPOTENT_RETRYABLE = frozenset({429})
_IDEMPOTENT_RETRYABLE = frozenset({429, 503})


class HttpClient:
    """A thin, synchronous Zendesk HTTP client.

    `transport` is injectable so tests use `httpx.MockTransport` and never touch
    the network - this is the layer `FakeBackend` cannot exercise.
    """

    def __init__(self, *, subdomain: str, email: str, api_token: str,
                 transport: httpx.BaseTransport | None = None,
                 timeout: float = 30.0) -> None:
        if not subdomain:
            raise ValueError(
                "a Zendesk subdomain is required; set ZENDESK_SUBDOMAIN. There is no "
                "default, deliberately: a hardcoded tenant is both a leak and a footgun.")
        if not email or not api_token:
            raise ValueError(
                "API-token auth needs both an email and a token; set "
                "CINO_CSA_ZENDESK_EMAIL and CINO_CSA_ZENDESK.")
        self._base = f"https://{subdomain}.zendesk.com"
        # Built once. Never logged, never repr'd, never placed in an exception.
        self._auth = "Basic " + base64.b64encode(
            f"{email}/token:{api_token}".encode()).decode()
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def __repr__(self) -> str:      # never let a credential reach a log line
        return f"HttpClient(base={self._base!r}, credential=<redacted>)"

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def request(self, method: str, path: str, *,
                params: Mapping[str, Any] | None = None,
                json: Mapping[str, Any] | None = None,
                idempotent: bool = True) -> dict[str, Any]:
        if params:
            # Refuse before the wire: mixing pagination styles returns a plausible
            # 200 with the sort silently dropped.
            check_params(params)
        sendable = {k: v for k, v in (params or {}).items() if v is not None}
        retryable = _IDEMPOTENT_RETRYABLE if idempotent else _NON_IDEMPOTENT_RETRYABLE

        attempt = 0
        while True:
            try:
                response = self._client.request(
                    method, f"{self._base}{path}", params=sendable, json=json,
                    headers={"Authorization": self._auth, "Accept": "application/json"})
            except httpx.HTTPError as e:
                # Chain the cause; keep the message free of anything credential-shaped.
                raise exc.ApiError(f"could not reach Zendesk: {type(e).__name__}") from e

            if response.status_code in retryable and attempt < MAX_RETRIES:
                error = parse_error(response.status_code, self._body_or_none(response),
                                    headers=dict(response.headers))
                wait = getattr(error, "retry_after", 10)
                attempt += 1
                log.warning("HTTP %s from Zendesk; retrying in %ss (attempt %s/%s)",
                            response.status_code, wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue

            if response.status_code >= 400:
                raise parse_error(response.status_code, self._body_or_none(response),
                                  headers=dict(response.headers))

            return self._envelope(response)

    @staticmethod
    def _body_or_none(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _envelope(response: httpx.Response) -> dict[str, Any]:
        """ZD-2: a 200 that looks wrong is an error, not something to hand downstream."""
        try:
            body = response.json()
        except ValueError as e:
            raise exc.ApiError(
                f"Zendesk returned HTTP {response.status_code} with a body that is not "
                f"JSON", status=response.status_code) from e
        if not isinstance(body, dict):
            raise exc.ApiError(
                f"Zendesk returned HTTP {response.status_code} with JSON that is not a "
                f"JSON object (got {type(body).__name__})", status=response.status_code)
        return body
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_http.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/_http.py tests/test_http.py
git commit -m "feat: HTTP client with asymmetric retry and a wrong-200 guard"
```

---

### Task 6: The `Backend` seam and `FakeBackend`

**Files:**
- Create: `src/csa_zendesk/backend.py`
- Create: `tests/test_backend.py`

**Interfaces:**
- Consumes: `HttpClient` (Task 5).
- Produces: `Envelope = dict[str, Any]`; `Backend` (a `runtime_checkable` `Protocol` declaring `get_ticket(*, ticket_id: int) -> Envelope`); `ApiBackend(http: HttpClient)`; `FakeBackend(tickets: dict[int, dict[str, Any]] | None = None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backend.py
import inspect

import httpx
import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk._http import HttpClient
from csa_zendesk.backend import ApiBackend, Backend, FakeBackend


def test_fake_backend_satisfies_the_protocol():
    assert isinstance(FakeBackend(), Backend)


def test_api_backend_satisfies_the_protocol():
    http = HttpClient(subdomain="example", email="agent@example.com", api_token="t",
                      transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert isinstance(ApiBackend(http), Backend)


def test_the_two_backends_have_identical_signatures():
    # FakeBackend powers every unit test, so a method that drifts from ApiBackend
    # would leave the whole suite exercising a stale double.
    for name in [n for n in dir(Backend) if not n.startswith("_")]:
        fake = inspect.signature(getattr(FakeBackend, name))
        real = inspect.signature(getattr(ApiBackend, name))
        assert fake == real, f"{name}: fake {fake} != real {real}"


def test_every_backend_method_takes_keyword_only_arguments():
    # PolicyBackend wraps uniformly; positional args would break that.
    for name in [n for n in dir(Backend) if not n.startswith("_")]:
        sig = inspect.signature(getattr(ApiBackend, name))
        for pname, p in sig.parameters.items():
            if pname == "self":
                continue
            assert p.kind is inspect.Parameter.KEYWORD_ONLY, f"{name}.{pname}"


def test_get_ticket_returns_the_raw_envelope():
    fake = FakeBackend(tickets={7: {"id": 7, "subject": "hello", "status": "open"}})
    env = fake.get_ticket(ticket_id=7)
    # RAW: the upstream envelope, not a model and not the inner object.
    assert env == {"ticket": {"id": 7, "subject": "hello", "status": "open"}}


def test_fake_backend_raises_the_same_error_type_as_the_real_one_for_a_missing_id():
    with pytest.raises(exc.NotFound):
        FakeBackend().get_ticket(ticket_id=999)


def test_api_backend_calls_the_documented_path():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ticket": {"id": 7}})
    http = HttpClient(subdomain="example", email="agent@example.com", api_token="t",
                      transport=httpx.MockTransport(handler))
    assert ApiBackend(http).get_ticket(ticket_id=7) == {"ticket": {"id": 7}}
    assert seen["path"] == "/api/v2/tickets/7.json"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'csa_zendesk.backend'`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/backend.py
"""The seam.

`Backend` is a Protocol. Every method is keyword-only, so `PolicyBackend` can wrap
uniformly, and every method returns the RAW upstream envelope - shaping belongs to
the delivery layer. That is what lets one wrapper gate every method, and it is why
ADR-002 rejects every object-mapping library.

`FakeBackend` powers every unit test. A method added to the Protocol or to
`ApiBackend` but not to the fake would leave the suite exercising a stale double,
so `tests/test_backend.py` compares their signatures.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from . import exceptions as exc
from ._http import HttpClient

Envelope = dict[str, Any]


@runtime_checkable
class Backend(Protocol):
    """Every Zendesk operation this library reaches, unshaped.

    Adding a method here obliges three things: an `ApiBackend` implementation, a
    `FakeBackend` implementation, and a `policy._GATES` entry. The gate table
    fails closed, so a missing entry turns the method off rather than leaving it
    ungoverned.
    """

    def get_ticket(self, *, ticket_id: int) -> Envelope: ...


class ApiBackend:
    """The real thing."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def get_ticket(self, *, ticket_id: int) -> Envelope:
        return self._http.get(f"/api/v2/tickets/{ticket_id}.json")


class FakeBackend:
    """In-memory double, faithful to the shapes observed live.

    It returns full envelopes (`{"ticket": {...}}`), not inner objects, and it
    raises the same typed errors the real backend does - a fake that fails
    differently is worse than no fake.
    """

    def __init__(self, tickets: dict[int, dict[str, Any]] | None = None) -> None:
        self.tickets: dict[int, dict[str, Any]] = dict(tickets or {})

    def get_ticket(self, *, ticket_id: int) -> Envelope:
        try:
            return {"ticket": dict(self.tickets[ticket_id])}
        except KeyError:
            raise exc.NotFound(f"no such record (ticket {ticket_id})") from None
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_backend.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_zendesk/backend.py tests/test_backend.py
git commit -m "feat: Backend protocol, ApiBackend and FakeBackend with a drift guard"
```

---

### Task 7: The fail-closed policy wrapper

**Files:**
- Create: `src/csa_zendesk/policy.py`
- Create: `tests/test_policy.py`

**Interfaces:**
- Consumes: `backend.Backend`, `exceptions.PolicyError`.
- Produces: capability constants (`TICKET_READ`, `TICKET_NOTE`, `TICKET_WRITE`, `TICKET_REPLY`, `TICKET_SOLVE`, `TICKET_CLOSE`, `TICKET_DELETE`, `TICKET_PURGE`, `PEOPLE_READ`, `PEOPLE_WRITE`, `PEOPLE_SUSPEND`, `PEOPLE_MERGE`, `PEOPLE_DELETE`, `PEOPLE_PURGE`, `HC_READ`, `HC_WRITE`, `HC_DELETE`, `ADMIN_READ`, `ADMIN_WRITE`, `ADMIN_DELETE`, `REPORTING_READ`, `REPORTING_EXPORT`, `RAW_READ`, `RAW_WRITE`, `BULK`); `ALL_CAPABILITIES: tuple[str, ...]`; `PROFILES: dict[str, frozenset[str]]`; `Gate` type alias; `_GATES: dict[str, Gate]`; `Policy(capabilities: frozenset[str])` with `.from_profile(name)` and `.missing(required) -> frozenset[str]`; `PolicyBackend(backend, policy)` exposing `.policy`.

`Gate` is `str | None | Callable[[dict[str, Any]], frozenset[str]]` — a constant capability, `None` for an ungated read, or a function of the call's kwargs. ADR-003 needs the third form: `update_ticket` requires `ticket.write`, *plus* `ticket.solve` when solving.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_policy.py
import inspect

import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk import policy as pol
from csa_zendesk.backend import Backend, FakeBackend


def wrapped(profile="default", tickets=None):
    return pol.PolicyBackend(FakeBackend(tickets=tickets or {1: {"id": 1}}),
                             pol.Policy.from_profile(profile))


def test_every_backend_method_has_a_declared_gate():
    # THE fail-closed guard. A method added to the Protocol without a gate must
    # fail CI here, not silently ship ungoverned.
    declared = {n for n in dir(Backend) if not n.startswith("_")}
    assert declared <= set(pol._GATES), f"undeclared: {declared - set(pol._GATES)}"


def test_a_method_with_no_gate_entry_is_refused_not_delegated():
    class Extra(FakeBackend):
        def undeclared_method(self, *, x: int) -> dict:  # pragma: no cover
            return {"x": x}
    pb = pol.PolicyBackend(Extra(), pol.Policy.from_profile("full"))
    with pytest.raises(exc.PolicyError, match="no declared capability gate"):
        pb.undeclared_method(x=1)


def test_the_default_profile_can_read_a_ticket():
    assert wrapped().get_ticket(ticket_id=1) == {"ticket": {"id": 1}}


def test_a_profile_without_the_capability_is_refused_with_a_remedy():
    pb = pol.PolicyBackend(FakeBackend({1: {"id": 1}}), pol.Policy(frozenset()))
    with pytest.raises(exc.PolicyError) as ei:
        pb.get_ticket(ticket_id=1)
    msg = str(ei.value)
    assert "ticket.read" in msg           # names the missing capability
    assert "CSA_ZENDESK_PROFILE" in msg   # names what an operator changes


def test_the_default_profile_holds_only_reversible_capabilities():
    default = pol.PROFILES["default"]
    for irreversible in (pol.TICKET_REPLY, pol.TICKET_SOLVE, pol.TICKET_CLOSE,
                         pol.TICKET_PURGE, pol.PEOPLE_MERGE, pol.PEOPLE_SUSPEND,
                         pol.PEOPLE_PURGE, pol.RAW_READ, pol.RAW_WRITE, pol.BULK):
        assert irreversible not in default, irreversible


def test_no_profile_grants_purge_close_merge_or_raw():
    never = {pol.TICKET_CLOSE, pol.TICKET_PURGE, pol.PEOPLE_PURGE,
             pol.PEOPLE_MERGE, pol.RAW_READ, pol.RAW_WRITE}
    for name, caps in pol.PROFILES.items():
        assert not (caps & never), f"profile {name!r} grants {sorted(caps & never)}"


def test_an_unknown_profile_is_a_loud_error_listing_the_real_ones():
    with pytest.raises(ValueError, match="unknown profile"):
        pol.Policy.from_profile("nope")


def test_a_callable_gate_computes_capabilities_from_the_kwargs():
    # ADR-003: one PUT, several authorities. Simulated here because update_ticket
    # itself arrives in Block 2.
    gate = lambda kw: frozenset({pol.TICKET_WRITE}) | (
        frozenset({pol.TICKET_SOLVE}) if kw.get("status") == "solved" else frozenset())
    assert gate({"priority": "high"}) == {pol.TICKET_WRITE}
    assert gate({"status": "solved"}) == {pol.TICKET_WRITE, pol.TICKET_SOLVE}


def test_missing_reports_every_absent_capability_not_just_the_first():
    p = pol.Policy(frozenset({pol.TICKET_WRITE}))
    assert p.missing(frozenset({pol.TICKET_WRITE, pol.TICKET_SOLVE, pol.TICKET_CLOSE})) == \
        {pol.TICKET_SOLVE, pol.TICKET_CLOSE}


def test_bulk_is_additive_never_a_substitute():
    # Granting the power to delete one ticket must not grant deleting a thousand.
    p = pol.Policy(frozenset({pol.TICKET_DELETE}))
    assert p.missing(frozenset({pol.TICKET_DELETE, pol.BULK})) == {pol.BULK}


def test_the_policy_cannot_be_widened_from_inside():
    pb = wrapped()
    with pytest.raises(AttributeError):
        pb._policy = pol.Policy.from_profile("full")  # type: ignore[misc]


def test_private_attributes_are_not_reachable_through_the_wrapper():
    with pytest.raises(AttributeError):
        wrapped()._backend  # noqa: B018


def test_capability_constants_and_the_all_tuple_agree():
    consts = {v for k, v in vars(pol).items()
              if k.isupper() and isinstance(v, str) and "." in v}
    assert consts == set(pol.ALL_CAPABILITIES)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'csa_zendesk.policy'`

- [ ] **Step 3: Implement**

```python
# src/csa_zendesk/policy.py
"""Capability gating, enforced by a wrapper around the Backend seam.

Two properties are load-bearing (ADR-002, ADR-003, ADR-010):

* **One wrapper.** Enforcement lives here, not in the tools, so a library
  embedder gets the same guarantee an MCP client does.
* **Fail closed.** `_GATES` must name every `Backend` method. An unlisted name is
  REFUSED, not delegated - so a newly added method arrives *off* rather than
  ungoverned, and forgetting a declaration turns a feature off instead of leaving
  a hole. `tests/test_policy.py` fails CI when the two drift.

Capabilities are `<domain>.<tier>`, ordered by whether the action can be undone,
and derived from a classification of all 822 in-scope operations rather than from
one workflow (ADR-010, `analysis/operation-classification.csv`).

`bulk` is CROSS-CUTTING: required *in addition* to the domain capability for any
`_many` / `/bulk` / `/import` operation. Granting the power to delete one ticket
does not grant the power to delete a thousand.

The policy cannot be widened in-band: no method changes it, and the configuration
is the complete permitted list rather than a delta.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Union

from . import exceptions as exc
from .backend import Backend

# --- capabilities, ordered by reversibility within each domain ---------------
TICKET_READ = "ticket.read"
TICKET_NOTE = "ticket.note"          # internal note; never leaves the org
TICKET_WRITE = "ticket.write"        # fields, assignee, tags; audited
TICKET_REPLY = "ticket.reply"        # PUBLIC comment; emailed, irreversible
TICKET_SOLVE = "ticket.solve"        # on-ramp to terminal: automation closes solved
TICKET_CLOSE = "ticket.close"        # terminal immediately; also covers merge
TICKET_DELETE = "ticket.delete"      # soft delete; recoverable with effort
TICKET_PURGE = "ticket.purge"        # "Delete Ticket Permanently"

PEOPLE_READ = "people.read"
PEOPLE_WRITE = "people.write"
PEOPLE_SUSPEND = "people.suspend"    # mark-as-spam suspends the REQUESTER
PEOPLE_MERGE = "people.merge"        # irreversible identity merge
PEOPLE_DELETE = "people.delete"
PEOPLE_PURGE = "people.purge"        # "Permanently Delete User"

HC_READ = "hc.read"
HC_WRITE = "hc.write"
HC_DELETE = "hc.delete"

# 225 configuration operations - 27% of the surface. Editing a trigger changes
# behaviour for every future ticket, silently. Splitting per object type is
# deferred (TODO B9) because admin is off by default in 1.0.
ADMIN_READ = "admin.read"
ADMIN_WRITE = "admin.write"
ADMIN_DELETE = "admin.delete"

REPORTING_READ = "reporting.read"
REPORTING_EXPORT = "reporting.export"

RAW_READ = "raw.read"                # the escape hatch; no profile grants it
RAW_WRITE = "raw.write"

BULK = "bulk"                        # cross-cutting; additive, never a substitute

ALL_CAPABILITIES: tuple[str, ...] = (
    TICKET_READ, TICKET_NOTE, TICKET_WRITE, TICKET_REPLY, TICKET_SOLVE, TICKET_CLOSE,
    TICKET_DELETE, TICKET_PURGE,
    PEOPLE_READ, PEOPLE_WRITE, PEOPLE_SUSPEND, PEOPLE_MERGE, PEOPLE_DELETE, PEOPLE_PURGE,
    HC_READ, HC_WRITE, HC_DELETE,
    ADMIN_READ, ADMIN_WRITE, ADMIN_DELETE,
    REPORTING_READ, REPORTING_EXPORT,
    RAW_READ, RAW_WRITE, BULK,
)

# Named profiles, because nobody composes a capability list correctly under time
# pressure and everybody can pick a word.
#
# `default` is everything that can be undone. Reply, solve, suspend, merge, delete,
# purge, bulk and the escape hatch are all opt-in - and close, purge, merge and raw
# are granted by NO profile, so enabling them is a deliberate act.
PROFILES: dict[str, frozenset[str]] = {
    "readonly": frozenset({TICKET_READ, HC_READ, PEOPLE_READ, REPORTING_READ, ADMIN_READ}),
    "default": frozenset({TICKET_READ, TICKET_NOTE, TICKET_WRITE,
                          HC_READ, PEOPLE_READ, REPORTING_READ, ADMIN_READ}),
    "agent": frozenset({TICKET_READ, TICKET_NOTE, TICKET_WRITE, TICKET_REPLY, TICKET_SOLVE,
                        HC_READ, PEOPLE_READ, REPORTING_READ}),
    "editor": frozenset({HC_READ, HC_WRITE, TICKET_READ, PEOPLE_READ}),
    "analyst": frozenset({TICKET_READ, PEOPLE_READ, HC_READ,
                          REPORTING_READ, REPORTING_EXPORT, ADMIN_READ}),
    # `full` is everything EXCEPT the four nobody should get by naming a word.
    "full": frozenset(ALL_CAPABILITIES) - {TICKET_CLOSE, TICKET_PURGE, PEOPLE_PURGE,
                                           PEOPLE_MERGE, RAW_READ, RAW_WRITE},
}

#: A gate is a constant capability, `None` for an ungated read, or a function of
#: the call's kwargs returning every capability that call requires. The third form
#: is what lets one `PUT` carry several authorities (ADR-003).
Gate = Union[str, None, Callable[[dict[str, Any]], frozenset[str]]]

#: Every Backend method needs an entry. Missing means REFUSED.
_GATES: dict[str, Gate] = {
    "get_ticket": TICKET_READ,
}


def _required(gate: Gate, kwargs: dict[str, Any]) -> frozenset[str]:
    if gate is None:
        return frozenset()
    if isinstance(gate, str):
        return frozenset({gate})
    return gate(kwargs)


class Policy:
    """An immutable set of granted capabilities."""

    __slots__ = ("capabilities",)

    def __init__(self, capabilities: frozenset[str]) -> None:
        object.__setattr__(self, "capabilities", frozenset(capabilities))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a Policy is immutable; construct a new one")

    def __repr__(self) -> str:
        return f"Policy({sorted(self.capabilities)!r})"

    @classmethod
    def from_profile(cls, name: str) -> Policy:
        try:
            return cls(PROFILES[name])
        except KeyError:
            raise ValueError(
                f"unknown profile {name!r}. Choose one of: "
                f"{', '.join(sorted(PROFILES))}") from None

    def missing(self, required: frozenset[str]) -> frozenset[str]:
        """Every required capability this policy does not grant. Empty means allowed."""
        return frozenset(required) - self.capabilities


class PolicyBackend:
    """Wraps a Backend and refuses anything the policy does not permit."""

    def __init__(self, backend: Backend, policy: Policy) -> None:
        object.__setattr__(self, "_backend", backend)
        object.__setattr__(self, "_policy", policy)

    def __setattr__(self, name: str, value: Any) -> None:
        # The policy cannot be widened from inside. Rebuild the wrapper instead.
        raise AttributeError("PolicyBackend is immutable; construct a new one")

    @property
    def policy(self) -> Policy:
        return object.__getattribute__(self, "_policy")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in _GATES:
            raise exc.PolicyError(
                f"`{name}` has no declared capability gate, so it is refused. This is a "
                f"programming error in csa-zendesk, not a configuration problem: add an "
                f"entry to policy._GATES.")
        backend = object.__getattribute__(self, "_backend")
        policy = object.__getattribute__(self, "_policy")
        gate, method = _GATES[name], getattr(backend, name)

        def guarded(**kwargs: Any) -> Any:
            absent = policy.missing(_required(gate, kwargs))
            if absent:
                raise exc.PolicyError(
                    f"`{name}` needs {', '.join(sorted(absent))}, which this install does "
                    f"not grant. Set CSA_ZENDESK_PROFILE to a profile that includes it — or "
                    f"for a capability no profile grants, list it explicitly in "
                    f"CSA_ZENDESK_CAPABILITIES — then restart. The policy cannot be changed "
                    f"from here.")
            return method(**kwargs)

        return guarded
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_policy.py -v`
Expected: 13 passed

- [ ] **Step 5: Mutation-test the fail-closed guard**

The most important control in the package. Prove it fires rather than trusting it.

```bash
# Add a method to the Protocol with no gate, and watch CI refuse it.
python3 - <<'EOF'
import pathlib
p = pathlib.Path("src/csa_zendesk/backend.py"); s = p.read_text()
p.write_text(s.replace("    def get_ticket(self, *, ticket_id: int) -> Envelope: ...",
    "    def get_ticket(self, *, ticket_id: int) -> Envelope: ...\n\n"
    "    def ungated_method(self, *, x: int) -> Envelope: ..."))
EOF
.venv/bin/python -m pytest tests/test_policy.py::test_every_backend_method_has_a_declared_gate -v
```
Expected: **FAIL**, naming `ungated_method`. Then restore:
```bash
git checkout src/csa_zendesk/backend.py
.venv/bin/python -m pytest tests/test_policy.py -q
```
Expected: 13 passed

- [ ] **Step 6: Commit**

```bash
git add src/csa_zendesk/policy.py tests/test_policy.py
git commit -m "feat: fail-closed capability policy with kwargs-dependent gates"
```

---

### Task 8: `ZendeskClient`, and the vertical wired together

**Files:**
- Create: `src/csa_zendesk/client.py`
- Modify: `src/csa_zendesk/__init__.py`
- Create: `tests/test_client.py`, `tests/test_vertical.py`
- Modify: `tests/test_public_api.py`

**Interfaces:**
- Consumes: everything from Tasks 2–7.
- Produces: `ZendeskClient(backend: Backend | PolicyBackend)` with `.get_ticket(*, ticket_id: int) -> Envelope` and `.policy -> Policy | None`; re-exported from the package root alongside `Backend`, `FakeBackend`, `ApiBackend`, `HttpClient`, `Policy`, `PolicyBackend`, and the exception types.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client.py
import pytest

from csa_zendesk import FakeBackend, Policy, PolicyBackend, ZendeskClient, exceptions as exc


def test_the_client_passes_envelopes_through_unshaped():
    c = ZendeskClient(FakeBackend({4: {"id": 4, "subject": "s"}}))
    assert c.get_ticket(ticket_id=4) == {"ticket": {"id": 4, "subject": "s"}}


def test_the_client_reports_the_active_policy_when_wrapped():
    c = ZendeskClient(PolicyBackend(FakeBackend(), Policy.from_profile("default")))
    assert c.policy is not None
    assert "ticket.read" in c.policy.capabilities


def test_the_client_reports_no_policy_for_a_bare_backend():
    # A library embedder may deliberately use an ungated backend. Say so honestly
    # rather than implying a policy exists.
    assert ZendeskClient(FakeBackend()).policy is None


def test_a_policy_refusal_reaches_the_caller_unchanged():
    c = ZendeskClient(PolicyBackend(FakeBackend({1: {"id": 1}}), Policy(frozenset())))
    with pytest.raises(exc.PolicyError):
        c.get_ticket(ticket_id=1)
```

```python
# tests/test_vertical.py
"""The point of Block 0: one method, every layer, no network."""
import httpx
import pytest

from csa_zendesk import (
    ApiBackend, HttpClient, Policy, PolicyBackend, ZendeskClient, exceptions as exc,
)


def build(handler, profile="default"):
    http = HttpClient(subdomain="example", email="agent@example.com", api_token="t",
                      transport=httpx.MockTransport(handler))
    return ZendeskClient(PolicyBackend(ApiBackend(http), Policy.from_profile(profile)))


def test_http_through_backend_through_policy_through_client():
    def handler(request):
        assert request.url.path == "/api/v2/tickets/12.json"
        return httpx.Response(200, json={"ticket": {"id": 12, "status": "open"}})
    assert build(handler).get_ticket(ticket_id=12) == {"ticket": {"id": 12, "status": "open"}}


def test_a_403_arrives_as_a_plan_boundary_not_an_outage():
    def handler(request):
        return httpx.Response(403, json={"error": {"title": "Forbidden",
                                                   "message": "You do not have access"}})
    with pytest.raises(exc.PlanBoundary):
        build(handler).get_ticket(ticket_id=1)


def test_the_two_404s_stay_distinguishable_all_the_way_up():
    def invalid(request):
        return httpx.Response(404, json={"error": "InvalidEndpoint", "description": "Not found"})

    def absent(request):
        return httpx.Response(404, json={"error": "RecordNotFound", "description": "Not found"})
    with pytest.raises(exc.EndpointNotAvailable):
        build(invalid).get_ticket(ticket_id=1)
    with pytest.raises(exc.NotFound):
        build(absent).get_ticket(ticket_id=1)


def test_the_policy_refuses_before_any_http_call_is_made():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"ticket": {}})
    with pytest.raises(exc.PolicyError):
        build(handler, profile="editor").get_ticket(ticket_id=1)
    assert calls["n"] == 0, "the refusal must not reach the network"
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_client.py tests/test_vertical.py -v`
Expected: FAIL — `ImportError: cannot import name 'FakeBackend' from 'csa_zendesk'`

- [ ] **Step 3: Implement the client**

```python
# src/csa_zendesk/client.py
"""`ZendeskClient` - the library's public surface. The MCP server is one consumer.

Thin by design: it forwards to a (usually policy-wrapped) Backend and returns raw
envelopes. Shaping into TypedDicts happens in the MCP tool layer, which is where
the model-facing contract lives.
"""
from __future__ import annotations

from typing import Any

from .backend import Backend, Envelope
from .policy import Policy, PolicyBackend


class ZendeskClient:
    """Typed, thin surface over a Backend.

    Accepts a bare `Backend` or a `PolicyBackend`. A bare backend is ungated, and
    `.policy` says so by returning None rather than implying a policy exists.
    """

    def __init__(self, backend: Backend | PolicyBackend) -> None:
        self._backend = backend

    @property
    def policy(self) -> Policy | None:
        """The active policy, or None for an ungated backend."""
        return getattr(self._backend, "policy", None)

    def get_ticket(self, *, ticket_id: int) -> Envelope:
        """One ticket, as the raw upstream envelope: `{"ticket": {...}}`."""
        result: Any = self._backend.get_ticket(ticket_id=ticket_id)
        return result
```

- [ ] **Step 4: Export the public API**

```python
# src/csa_zendesk/__init__.py
"""A Python library and local stdio MCP server over the Zendesk REST API.

Block 0 is a vertical slice: HTTP, the error taxonomy, pagination, the Backend
seam, the fail-closed policy wrapper and the client, proven on `get_ticket`.
Nothing here touches MCP; that starts in Block 1.
"""
from __future__ import annotations

from . import exceptions
from ._http import HttpClient
from .backend import ApiBackend, Backend, Envelope, FakeBackend
from .client import ZendeskClient
from .policy import ALL_CAPABILITIES, PROFILES, Policy, PolicyBackend

__version__ = "0.0.1"

__all__ = [
    "ALL_CAPABILITIES", "ApiBackend", "Backend", "Envelope", "FakeBackend",
    "HttpClient", "PROFILES", "Policy", "PolicyBackend", "ZendeskClient",
    "__version__", "exceptions",
]
```

- [ ] **Step 5: Extend the public-API test**

```python
# append to tests/test_public_api.py
def test_the_types_an_embedder_needs_are_exported():
    # Anyone writing a custom Backend or policy needs these by name.
    for name in ("Backend", "Envelope", "FakeBackend", "ApiBackend", "HttpClient",
                 "Policy", "PolicyBackend", "ZendeskClient", "exceptions",
                 "PROFILES", "ALL_CAPABILITIES"):
        assert name in csa_zendesk.__all__, name


def test_nothing_writes_to_stdout_on_import():
    # Under stdio MCP, stdout IS the JSON-RPC channel. One stray byte corrupts the
    # session and the server looks alive while answering nothing.
    import importlib
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        importlib.reload(csa_zendesk)
    assert buf.getvalue() == ""
```

- [ ] **Step 6: Run the whole suite with coverage**

Run: `.venv/bin/python -m pytest -q --cov=csa_zendesk --cov-report=term-missing --cov-fail-under=90`
Expected: all pass, coverage ≥ 90%. If a branch is uncovered, add the test rather than lowering the floor.

- [ ] **Step 7: Lint, types, and the repository gates**

```bash
.venv/bin/ruff check src tests scripts
.venv/bin/mypy
python3 scripts/check_public_safe.py
python3 scripts/check_spec.py
```
Expected: all clean.

- [ ] **Step 8: Commit and open the PR**

```bash
git add src tests
git commit -m "feat: ZendeskClient and the Block 0 vertical, wired end to end"
git push -u origin feat/block-0-foundations
gh pr create --title "feat: Block 0 — foundations" --body-file /tmp/block0_pr.md
```

---

## Self-review

Run after the plan is written, before execution.

**1. Spec coverage.** Block 0's row in the spec's §9 lists: package skeleton, CI (lint, types, tests, coverage, security), `_http`, `_errors`, `_pagination`, `Backend` + `FakeBackend`, `policy` skeleton, API-token auth, `get_ticket` end to end. Tasks 1–8 cover each. **Deliberately deferred to Block 0b:** OAuth, the token store, refresh, `whoami`. **Deliberately absent from Block 0:** `_content.py` (injection wrapping — Block 1, where content first reaches a model), `_aggregate.py` (Block 5), everything under `mcp/` (Block 1), the escape hatch's covered-path table (Block 6).

**2. Placeholders.** None: every code step carries runnable code, every test step names the command and the expected result.

**3. Type consistency.** `Envelope = dict[str, Any]` is defined once in `backend.py` and imported elsewhere. `Gate` is defined in `policy.py` and used only there. `HttpClient.request(...)` is called by `ApiBackend` with the signature Task 5 declares. `Policy.missing()` returns `frozenset[str]` and is consumed as such in `PolicyBackend`. `FakeBackend(tickets=...)` takes the same keyword in every test.

**4. One known gap, recorded rather than hidden.** The CI `gates` job runs `check_public_safe.py` with **structural patterns only** — the tenant term list is gitignored and absent in CI. That is the designed behaviour (it reports reduced coverage rather than a false pass), but it means the literal-term tier is enforced only by the local pre-commit hook. Worth a `TODO` item; not a blocker for Block 0.
