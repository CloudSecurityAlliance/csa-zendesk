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

Never interpolate a credential into a message here: only response bodies and
headers pass through this module, and embedders log the exceptions it returns.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import exceptions as exc

DEFAULT_RETRY_AFTER = 10


def extract_problems(body: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Pull `details` out as a field -> problems map, defensively.

    Anything that is not a map of lists-of-maps is discarded rather than trusted:
    a malformed `details` must not become a crash inside error handling.
    """
    details = body.get("details")
    if not isinstance(details, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for field, items in details.items():
        if isinstance(items, list) and all(isinstance(i, dict) for i in items):
            out[str(field)] = [dict(i) for i in items]
    return out


def _message(body: dict[str, Any]) -> str:
    """Best available human text, trying every key Zendesk uses."""
    err = body.get("error")
    if isinstance(err, dict):
        parts = [str(err.get(k, "")) for k in ("title", "message")]
        return " - ".join(p for p in parts if p) or "error"
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        parts = [str(first.get(k, "")) for k in ("code", "title", "detail")]
        return " - ".join(p for p in parts if p) or "error"
    parts = [str(body.get(k, "")) for k in ("error", "description", "message")]
    return " - ".join(p for p in parts if p) or "error"


def _code(body: dict[str, Any]) -> str:
    """The machine-readable code, wherever this envelope keeps it."""
    err = body.get("error")
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        return str(err.get("title", ""))
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("code", ""))
    return ""


def _retry_after(headers: Mapping[str, str] | None) -> int:
    """Honour `Retry-After`; default to 10s when it is absent, per the official Ruby client.

    Case-insensitive by its own doing rather than by trusting the caller: an httpx response
    hands over a case-insensitive mapping, but a plain dict does not, and silently losing
    the header would fall back to the default while looking like it had read one.

    Clamped at zero. A negative value is nonsense from the server's side, but it would
    reach `time.sleep()` in the retry loop and raise there instead of here.
    """
    if not headers:
        return DEFAULT_RETRY_AFTER
    raw: str | None = None
    for key, value in headers.items():
        if key.lower() == "retry-after":
            raw = value
            break
    try:
        return max(0, int(str(raw)))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER


def parse_error(status: int, body: object, *, headers: Mapping[str, str] | None = None) -> exc.ZendeskError:
    """Map a failed response to a typed exception. Never raises."""
    if status == 429:
        return exc.RateLimited("Zendesk rate limit reached", retry_after=_retry_after(headers))
    if status == 503:
        return exc.ServiceUnavailable("Zendesk is unavailable, likely maintenance", retry_after=_retry_after(headers))

    # ZD-2: a body that is not a JSON object is an error, not something to hand on.
    if not isinstance(body, dict):
        return exc.ApiError(f"Zendesk returned HTTP {status} with a body that is not a JSON object", status=status)

    code, message = _code(body), _message(body)

    if status == 401:
        # Not "or re-run authorisation" - there is no re-auth flow to run yet (this
        # is a static API token, not OAuth), so naming one would advertise a remedy
        # that does not exist. Check the two env vars that actually are read.
        return exc.CredentialsRejected(
            f"Zendesk rejected the credential ({message}). Check CINO_CSA_ZENDESK and CINO_CSA_ZENDESK_EMAIL."
        )
    if status == 403:
        return exc.PlanBoundary(
            f"Zendesk refused this endpoint for this account ({message}). This is a plan "
            f"or feature boundary, not an outage and not a bad request."
        )
    if status == 404:
        if code == "InvalidEndpoint":
            return exc.EndpointNotAvailable(f"this endpoint is not available on this account ({message})")
        return exc.NotFound(f"no such record ({message})")
    if status == 422:
        if "Search Response Limit" in message:
            return exc.SearchLimitExceeded(
                "search refuses past 1000 results, however large the reported count; use the export path for more"
            )
        problems = extract_problems(body)
        if code == "RecordInvalid" or problems:
            return exc.ValidationError("Zendesk refused the record", problems=problems)
        return exc.ApiError(f"Zendesk rejected the request: {message}", status=status)
    if status == 400 and code in ("InvalidPaginationDepth", "InvalidPaginationParameter"):
        return exc.PaginationError(f"{code}: {message}")

    return exc.ApiError(f"Zendesk returned HTTP {status}: {message}", status=status)
