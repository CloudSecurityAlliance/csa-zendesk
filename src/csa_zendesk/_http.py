"""HTTP transport: auth, retries, and the rule that a wrong-looking 200 is an error.

Deliberately small. ADR-002 records that if this file passes roughly 400 lines we
are writing a client library by accident and should say so.

The retry rules are not symmetric, and the asymmetry is the point:

  429  always retryable - the request was REFUSED, so nothing was applied.
  503  retryable for idempotent requests only. For a non-idempotent write the
       mutation may already have landed, and retrying could double-apply it.

A `Retry-After` the server hands back is honoured, not trusted blindly: it can be
enormous (999999 has been observed live), and a stdio MCP server that blocks for
days is indistinguishable from a hung one. `MAX_RETRY_AFTER_SECONDS` is the ceiling
we will actually wait on for any *one* sleep. When the server asks for longer than
that we do not sleep and do not silently shorten the wait - we stop retrying and
raise the error we were handed, exactly as `_errors.parse_error` reported it.

That per-sleep ceiling is not enough by itself: `MAX_RETRIES` retries at up to
`MAX_RETRY_AFTER_SECONDS` each still allows one logical call to sleep for
`MAX_RETRIES * MAX_RETRY_AFTER_SECONDS` seconds in total, and Zendesk's account rate
limit resets on a per-minute window, so a `Retry-After` at or near the per-sleep cap
three times in a row is an ordinary production sequence, not a pathological one.
`MAX_TOTAL_RETRY_SECONDS` bounds the *sum* of every sleep this client performs across
one logical call, so the two ceilings answer different questions: the per-sleep cap
asks "is this one wait absurd?" and the budget asks "have we, cumulatively, been
silent for too long?" - and a caller can hit either first depending on the shape of
the responses it gets.

The retry schedule is exercised in tests by monkeypatching `time.sleep` on this
module (`monkeypatch.setattr(_http.time, "sleep", ...)`), not through a constructor
parameter - there is deliberately no `sleep=` argument on `HttpClient`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from . import exceptions as exc
from ._errors import parse_error
from ._pagination import check_params

log = logging.getLogger(__name__)

#: Attempts beyond the first that a retryable response may consume. Bounds the loop:
#: at most 1 + MAX_RETRIES requests are ever sent for one logical call.
MAX_RETRIES = 3

#: The longest `Retry-After` this client will actually sleep for. A value larger than
#: this is honoured in the exception (never shortened) but not waited out.
MAX_RETRY_AFTER_SECONDS = 60

#: The longest total time this client will spend sleeping across every retry of one
#: logical call. 90s: enough headroom for one full per-sleep-cap wait (60s) plus a
#: second, more modest one, while staying well short of MAX_RETRIES * MAX_RETRY_AFTER_SECONDS
#: (180s) - a wait most interactive MCP callers' own client-side timeouts will not
#: survive, and this module's own reasoning about MAX_RETRY_AFTER_SECONDS applies just
#: as much to the sum as to any one term of it: a client silent for three minutes is
#: indistinguishable from a hung one.
MAX_TOTAL_RETRY_SECONDS = 90

#: 429 means the request was refused outright - nothing was applied, so retrying a
#: write is safe.
_NON_IDEMPOTENT_RETRYABLE = frozenset({429})

#: 503 additionally retryable, but only when the caller says the request is
#: idempotent - a write may have landed before the maintenance window closed.
_IDEMPOTENT_RETRYABLE = frozenset({429, 503})


def _is_invalid_token(response: httpx.Response) -> bool:
    """True only for Zendesk's `invalid_token`, never for a scope or permission 401.

    A positive match on one discriminator, not a blanket "was this a 401" - the
    envelope is not uniform (API-SURFACE.md §5.5), so this must not assume one
    shape and must not raise on a malformed or empty body; it falls through to
    "not invalid_token" instead.
    """
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("error") == "invalid_token"


class HttpClient:
    """A thin, synchronous Zendesk HTTP client.

    `transport` is injectable so tests use `httpx.MockTransport` and never touch
    the network - this is the layer `FakeBackend` cannot exercise.

    **No credential is stored at all.** Since ADR-015 this client authenticates
    only by OAuth, and it holds a `token_provider` rather than a token: each
    access token exists for the lifetime of one request and is never written to
    an instance attribute. The provider itself is captured only in an httpx
    request-hook closure, so `vars(client)`, a `__dict__` walker,
    `json.dumps(vars(obj))`, a crash-reporter object dump, `copy.copy`, and
    `pickle` all come up empty - none of them can see inside a closure cell.

    This is **not** protection against a debugger or against something that
    specifically walks `__closure__`; that is not achievable for an object that
    must reach a usable credential to do its job. The goal is narrower and stated
    plainly: no *ordinary* observation path reveals it. Holding a provider rather
    than a token does narrow the window though - there is no long-lived secret in
    this object to observe, only a callable that can fetch one.
    """

    def __init__(
        self,
        subdomain: str,
        token_provider: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        on_invalid_token: Callable[[], None] | None = None,
    ) -> None:
        """
        `token_provider` returns a current OAuth access token and is called on
        **every** request rather than once here (ADR-009, ADR-015).

        That is not indirection for its own sake. Zendesk issues a 30-minute
        `expires_in` automatically to any client created on or after 2026-04-30,
        so a token captured at construction expires mid-session; the provider is
        where Block 0b's refresh-before-expiry lives, and putting it here now
        means the refresh machinery arrives behind this signature instead of
        changing it.

        There is no API-token path. ADR-015 removed it: Zendesk stops issuing
        API tokens on 2026-10-27 and stops honouring them on 2027-04-30, and a
        fallback that silently activates when OAuth is misconfigured turns an
        auth failure into something that reads like a permissions failure -
        which is the confusion the 401 handling below goes to some trouble to
        prevent.

        `on_invalid_token`, if given, is called once and the request retried once
        when Zendesk rejects a token as `invalid_token` despite it looking
        unexpired (ADR-009's reactive path). `None` by default: the 401 then
        surfaces as `CredentialsRejected`, today's behaviour, unchanged.
        """
        if not subdomain:
            raise ValueError(
                "a Zendesk subdomain is required; set CSA_ZENDESK_SUBDOMAIN. There is no "
                "default, deliberately: a hardcoded tenant is both a leak and a footgun."
            )
        if not callable(token_provider):
            raise ValueError(
                "token_provider must be a callable returning a current OAuth access token. "
                "A bare string will not do: access tokens expire in 30 minutes, so the "
                "credential has to be re-read per request rather than captured once."
            )
        self._host = f"{subdomain}.zendesk.com"
        self._base = f"https://{self._host}"
        self._on_invalid_token = on_invalid_token

        # The provider lives only in this closure's cell, never in self.__dict__,
        # and no token is stored at all - each one exists for the lifetime of a
        # single request. Once __init__ returns, the only way to reach the
        # provider is through _authorize.__closure__, which is exactly the
        # residual, unavoidable path the class docstring names.
        def _authorize(request: httpx.Request) -> None:
            token = token_provider()
            if not token:
                raise exc.CredentialsRejected(
                    "the token provider returned an empty access token. Re-authorise; "
                    "if this persists the stored refresh token is probably revoked."
                )
            request.headers["Authorization"] = "Bearer " + token

        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={"Accept": "application/json"},
            event_hooks={"request": [_authorize]},
        )

    def __repr__(self) -> str:  # never let a credential reach a log line
        return f"HttpClient(base={self._base!r}, credential=<redacted>)"

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        idempotent: bool = True,
    ) -> dict[str, Any]:
        # Refuse before the wire: mixing pagination styles returns a plausible 200
        # with the sort silently dropped. Called on every request, not just ones a
        # caller remembered carry params.
        check_params(params or {})
        sendable = {k: v for k, v in (params or {}).items() if v is not None}
        retryable = _IDEMPOTENT_RETRYABLE if idempotent else _NON_IDEMPOTENT_RETRYABLE

        attempt = 0
        slept = 0  # cumulative seconds actually spent sleeping in this call, so far
        retried_auth = False  # ADR-009: at most one refresh-and-retry per logical call
        while True:
            response = self._send(method, path, params=sendable, json=json)

            if response.status_code < 400:
                return self._envelope(response)

            # ADR-009: refresh on rejection, retried ONCE, and only when Zendesk says
            # `invalid_token`. A 401 or 403 arising from scope or from the operator's
            # own Zendesk permissions is passed through unchanged, so a permissions
            # problem stays visible as a permissions problem rather than looking like
            # auth flakiness.
            if (
                response.status_code == 401
                and not retried_auth
                and self._on_invalid_token is not None
                and _is_invalid_token(response)
            ):
                retried_auth = True
                self._on_invalid_token()
                continue

            error = parse_error(response.status_code, self._body_or_none(response), headers=dict(response.headers))

            if (
                response.status_code in retryable
                and attempt < MAX_RETRIES
                and isinstance(error, (exc.RateLimited, exc.ServiceUnavailable))
            ):
                wait = error.retry_after
                if wait <= MAX_RETRY_AFTER_SECONDS:
                    if slept + wait <= MAX_TOTAL_RETRY_SECONDS:
                        attempt += 1
                        slept += wait
                        log.warning(
                            "HTTP %s from Zendesk; retrying in %ss (attempt %s/%s, %ss/%ss of retry budget spent)",
                            response.status_code,
                            wait,
                            attempt,
                            MAX_RETRIES,
                            slept,
                            MAX_TOTAL_RETRY_SECONDS,
                        )
                        time.sleep(wait)
                        continue
                    raise self._budget_exhausted(error, slept, wait)

            raise error

    @staticmethod
    def _validate_path(path: str) -> None:
        """Refuse a `path` shaped to redirect this credentialed request, or to
        carry a query string that would be silently discarded.

        Probe-verified live: with `path` built into the URL by simple
        concatenation (`f"{base}{path}"`), each of these sends the credentialed
        request to a DIFFERENT host, not the tenant's - `@evil.example.net/x`
        (the tenant host becomes URL userinfo, `evil.example.net` becomes the
        host), `.evil.example.net/x` (host becomes
        `<tenant>.zendesk.com.evil.example.net`), and `https://evil.example.net/x`
        (host becomes `<tenant>.zendesk.comhttps`, still resolvable, still wrong).
        Not reachable while every caller interpolates an int; it becomes reachable
        the moment a model-supplied path exists, and ticket content is this
        project's named primary risk. Rejected outright rather than sanitised - a
        caller who passed a hostile path should be told, not quietly corrected -
        and `HttpClient._send` additionally verifies the *built* URL resolves to
        the tenant host, so this check is pattern-based defense first, not the
        only defense.

        A `?` or `#` embedded in `path` is a separate hazard: it is silently
        discarded the moment a `params=` dict (even an empty one) is also passed
        to httpx, which is precisely the mixed-pagination defect class
        `check_params` exists to prevent, arriving through the one parameter
        `check_params` cannot see. Refused rather than merged - merging would
        create two ways to say the same thing and a silent precedence rule.
        """
        if not path.startswith("/") or path.startswith("//"):
            raise exc.InvalidPath(
                f"refusing path {path!r}: it must start with exactly one '/'. A path with "
                f"no leading slash, or a leading '//', can resolve to a host other than the "
                f"tenant's - pass an absolute, single-origin path instead."
            )
        if "@" in path:
            raise exc.InvalidPath(
                f"refusing path {path!r}: it contains '@', which can move the tenant host "
                f"into the URL's userinfo and hand this request's credential to whatever "
                f"host follows it instead."
            )
        if "?" in path or "#" in path:
            raise exc.InvalidPath(
                f"refusing path {path!r}: it contains '?' or '#'. A query string or "
                f"fragment embedded in `path` is silently dropped rather than sent - pass "
                f"query parameters via params= instead, so they can be checked."
            )

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any],
        json: Mapping[str, Any] | None,
    ) -> httpx.Response:
        """Issue one request, translating a transport failure without chaining it.

        `e` here is an httpx exception whose `.request.headers` carries the live
        `Authorization` header. Neither `__cause__` (an explicit `raise ... from e`)
        NOR `__context__` (Python's *implicit* chaining, which `from None` alone does
        not clear - it only clears `__cause__` and a display flag) may end up
        referencing it: a crash reporter or error tracker commonly walks whichever of
        the two is set. The fix is structural rather than a `from` clause: this method
        raises only after its own `except` clause has finished, by which point no
        exception is being handled, so the interpreter attaches no context at all.
        The exception's class name, plus the method and path - never the query
        string, never a header - go into the message instead.

        The Authorization header is attached by an event hook that fires inside
        `Client.send`, not inside `Client.build_request` - so building the request
        first and checking its resolved host before calling `send` means a request
        that fails the host check never carries the credential in the first place,
        not merely "the credential is discarded after being attached".

        `httpx.InvalidURL` (a malformed `path` that survives `_validate_path` but
        that httpx itself refuses to parse, e.g. a control character) is not an
        `httpx.HTTPError` subclass and would otherwise escape this module untyped -
        folded into the same handling as a transport failure, since both mean the
        request could not be formed or sent.
        """
        self._validate_path(path)
        url = f"{self._base}{path}"
        try:
            request = self._client.build_request(method, url, params=params, json=json)
            if request.url.host != self._host:
                # Belt-and-braces: verify by construction, not just by pattern. The
                # cost is one comparison per request; the failure mode this catches
                # is a credential sent to an attacker.
                raise exc.InvalidPath(
                    f"refusing to send {method} {path}: it resolves to host "
                    f"{request.url.host!r}, not the tenant host {self._host!r}."
                )
            response = self._client.send(request)
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            kind = type(e).__name__
        else:
            return response
        raise exc.ApiError(f"could not reach Zendesk ({kind}) requesting {method} {path}")

    @staticmethod
    def _budget_exhausted(
        error: exc.RateLimited | exc.ServiceUnavailable, slept: int, next_wait: int
    ) -> exc.RateLimited | exc.ServiceUnavailable:
        """Re-raise the same typed error - same type, same `retry_after` - with a
        message that names the real reason for giving up.

        Not `raise error` unmodified: a caller who waited most of a minute needs to
        know this client gave up on its own retry budget, not that Zendesk itself
        refused a third or fourth time - those call for different remedies, and this
        project's rule is that every refusal names its own. The remedy here is
        exactly that distinction: wait longer than this client will, then retry.
        """
        cls = type(error)
        message = (
            f"{error} - giving up after a cumulative retry budget of {MAX_TOTAL_RETRY_SECONDS}s "
            f"of sleeping (already spent {slept}s; the next wait would be {next_wait}s more). "
            f"This client gave up on its own budget, not because Zendesk refused again - "
            f"wait longer than this client will, then retry."
        )
        return cls(message, retry_after=error.retry_after)

    @staticmethod
    def _body_or_none(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _envelope(response: httpx.Response) -> dict[str, Any]:
        """ZD-2: a 200 that looks wrong is an error - but "no content" is not "wrong".

        Zendesk documents `204 No Content` for deletes (122 of the 882 inventoried
        operations are DELETE), and a `200` can arrive with a zero-length body too -
        the same situation by another status code. Both are a success with nothing
        to report, which is a different answer from a body of the *wrong shape*
        (an array, a bare string, a number, `null`): that case must keep failing.
        Judged on the evidence - whether there is any body at all - not on
        `status == 204` alone, since a 200 can be exactly as empty.
        """
        if not response.content.strip():
            return {}
        try:
            body = response.json()
        except ValueError as e:
            raise exc.ApiError(
                f"Zendesk returned HTTP {response.status_code} with a body that is not JSON",
                status=response.status_code,
            ) from e
        if not isinstance(body, dict):
            raise exc.ApiError(
                f"Zendesk returned HTTP {response.status_code} with JSON that is not a JSON "
                f"object (got {type(body).__name__})",
                status=response.status_code,
            )
        return body
