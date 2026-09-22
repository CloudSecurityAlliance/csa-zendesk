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
    "ZendeskError",
    "CredentialsRejected",
    "PlanBoundary",
    "EndpointNotAvailable",
    "NotFound",
    "ValidationError",
    "PaginationError",
    "InvalidPath",
    "SearchLimitExceeded",
    "EmptyWrite",
    "InvalidFilename",
    "RateLimited",
    "ServiceUnavailable",
    "PolicyError",
    "ApiError",
]


class ZendeskError(Exception):
    """Base for everything this package raises."""


class CredentialsRejected(ZendeskError):
    """The credential is absent, wrong, or expired. Not a scope problem.

    `remedy`, keyword-only and `None` by default, is this library's OWN
    guidance text - never interpolated from a Zendesk response body - kept out
    of `message` (and so out of `str(self)`) so a delivery layer that wraps
    vendor-derived text as untrusted (`server.py`'s `_on_call_tool`, via
    `_untrusted.wrap`) can leave it unwrapped instead of splicing trusted and
    untrusted prose into one opaque wrapped blob (fix wave I7). Only one raise
    site sets it: `_errors.parse_error`'s 401 branch, whose `message`
    interpolates Zendesk's own error text and is genuinely mixed. The other
    raise site (`_http.py`'s empty-access-token check) composes no vendor text
    at all, so it has nothing to separate and leaves this `None`.
    """

    def __init__(self, message: str, *, remedy: str | None = None) -> None:
        self.remedy = remedy
        super().__init__(message)


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


class InvalidPath(ZendeskError):
    """A `path` argument refused before any request was built or sent.

    Two shapes trigger this, both caught in `HttpClient` before the credentialed
    request touches the wire: a path shaped to move this request to a different
    host (no leading `/`, a leading `//`, an `@`, or a construction that resolves
    to a host other than the tenant's), and a path carrying its own `?` or `#` -
    which this client's own request building silently discards rather than sends,
    the exact defect class `check_params` exists to prevent, arriving through the
    one parameter it cannot see. Refused rather than sanitised: a caller who passed
    a hostile or malformed path should be told, not quietly corrected.
    """


class SearchLimitExceeded(ZendeskError):
    """Search refuses past 1000 results, however large the reported `count`."""


class EmptyWrite(ZendeskError):
    """A write call refused before it reached the wire because, as given, it
    would change nothing: `backend.assign_ticket` naming neither `assignee_id`
    nor `group_id`, or `backend.update_ticket` with an empty `fields` mapping.

    Raised by `backend.py`'s `_refuse_an_empty_assignment`,
    `_refuse_an_empty_update`, `_refuse_an_empty_note` and
    `_refuse_an_empty_upload` (one exception for all four - the same defect in
    sibling methods, not four near-identical types), each building its message
    from a
    fixed sentence naming what the call needed - never text out of a Zendesk
    response, since nothing has been sent yet. An empty-body write is not
    free just because it changes nothing: it spends this tenant's write-rate
    budget and lands in Zendesk's own audit log as an update that changed
    nothing, undermining the very legibility agent writes are meant to have
    there.
    """


class InvalidFilename(ZendeskError):
    """An `upload_file` filename was refused before it reached the wire.

    Zendesk's upload documentation requires the filename passed here to share
    an extension with the real file's content: "While the two names can be
    different, their file extensions must be the same. If they don't match,
    the agent's browser or file reader could give an error when attempting to
    open the attachment." A filename with no extension at all cannot satisfy
    that, so it is refused here - own prose, nothing derived from a Zendesk
    response, since nothing has been sent yet - rather than accepted and left
    to surface later as an unopenable attachment instead of a clean error.

    Raised by `backend._refuse_a_filename_without_extension`, shared by
    `ApiBackend` and `FakeBackend` the same way `EmptyWrite`'s siblings are.
    """


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
