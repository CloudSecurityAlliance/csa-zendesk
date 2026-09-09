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
we will actually wait on. When the server asks for longer than that we do not sleep
and do not silently shorten the wait - we stop retrying and raise the error we were
handed, exactly as `_errors.parse_error` reported it.
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

#: Attempts beyond the first that a retryable response may consume. Bounds the loop:
#: at most 1 + MAX_RETRIES requests are ever sent for one logical call.
MAX_RETRIES = 3

#: The longest `Retry-After` this client will actually sleep for. A value larger than
#: this is honoured in the exception (never shortened) but not waited out.
MAX_RETRY_AFTER_SECONDS = 60

#: 429 means the request was refused outright - nothing was applied, so retrying a
#: write is safe.
_NON_IDEMPOTENT_RETRYABLE = frozenset({429})

#: 503 additionally retryable, but only when the caller says the request is
#: idempotent - a write may have landed before the maintenance window closed.
_IDEMPOTENT_RETRYABLE = frozenset({429, 503})


class HttpClient:
    """A thin, synchronous Zendesk HTTP client.

    `transport` is injectable so tests use `httpx.MockTransport` and never touch
    the network - this is the layer `FakeBackend` cannot exercise.

    The credential is deliberately **not** an instance attribute. It is built once
    inside `__init__` and captured only in an httpx request-hook closure, so
    `vars(client)`, a `__dict__` walker, `json.dumps(vars(obj))`, a crash-reporter
    object dump, `copy.copy`, and `pickle` all come up empty - none of them can see
    inside a closure cell. This is **not** protection against a debugger or against
    something that specifically walks `__closure__`; that is not achievable for an
    object that must hold a usable credential to do its job (httpx's own
    `BasicAuth` holds one too, the same way). The goal is narrower and stated
    plainly: no *ordinary* observation path reveals it.
    """

    def __init__(
        self,
        subdomain: str,
        email: str,
        api_token: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not subdomain:
            raise ValueError(
                "a Zendesk subdomain is required; set ZENDESK_SUBDOMAIN. There is no "
                "default, deliberately: a hardcoded tenant is both a leak and a footgun."
            )
        if not email or not api_token:
            raise ValueError(
                "API-token auth needs both an email and a token; set CINO_CSA_ZENDESK_EMAIL and CINO_CSA_ZENDESK."
            )
        self._base = f"https://{subdomain}.zendesk.com"

        # The credential lives only in this closure's cell, never in self.__dict__.
        # `header` is a local variable of __init__ - once __init__ returns, the only
        # way to reach it is through _authorize.__closure__, which is exactly the
        # residual, unavoidable path the class docstring names.
        header = "Basic " + base64.b64encode(f"{email}/token:{api_token}".encode()).decode()

        def _authorize(request: httpx.Request) -> None:
            request.headers["Authorization"] = header

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
        while True:
            response = self._send(method, path, params=sendable, json=json)

            if response.status_code < 400:
                return self._envelope(response)

            error = parse_error(response.status_code, self._body_or_none(response), headers=dict(response.headers))

            if (
                response.status_code in retryable
                and attempt < MAX_RETRIES
                and isinstance(error, (exc.RateLimited, exc.ServiceUnavailable))
            ):
                wait = error.retry_after
                if wait <= MAX_RETRY_AFTER_SECONDS:
                    attempt += 1
                    log.warning(
                        "HTTP %s from Zendesk; retrying in %ss (attempt %s/%s)",
                        response.status_code,
                        wait,
                        attempt,
                        MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue

            raise error

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
        """
        try:
            response = self._client.request(method, f"{self._base}{path}", params=params, json=json)
        except httpx.HTTPError as e:
            kind = type(e).__name__
        else:
            return response
        raise exc.ApiError(f"could not reach Zendesk ({kind}) requesting {method} {path}")

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
