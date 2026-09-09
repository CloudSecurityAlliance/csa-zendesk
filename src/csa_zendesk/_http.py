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
        # Built once. Never logged, never repr'd, never placed in an exception.
        self._auth = "Basic " + base64.b64encode(f"{email}/token:{api_token}".encode()).decode()
        self._client = httpx.Client(transport=transport, timeout=timeout)

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
            try:
                response = self._client.request(
                    method,
                    f"{self._base}{path}",
                    params=sendable,
                    json=json,
                    headers={"Authorization": self._auth, "Accept": "application/json"},
                )
            except httpx.HTTPError as e:
                # Chain the cause; keep the message free of anything credential-shaped.
                raise exc.ApiError(f"could not reach Zendesk: {type(e).__name__}") from e

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
