"""HTTP client: the Zendesk envelope, path safety, and dispatch to `_transport`.

Deliberately small. ADR-002 records that if this file passes roughly 400 lines we
are writing a client library by accident and should say so - which is exactly
what E8 (TODO.md) found once it happened, and why the auth/retry machinery now
lives in `_transport.py` instead of here. This module keeps only what actually
needs to know what a Zendesk response looks like: path safety (so a
model-supplied path can never be redirected), the envelope rule (a 200 that
looks wrong is an error, but "no content" is not "wrong"), and the public
`get`/`request`/`post_binary` surface.
"""

from __future__ import annotations

# Re-exported, not used below: kept importable as `_http.time` and `_http.MAX_*`
# solely because the existing test suite reaches them that way (e.g.
# `monkeypatch.setattr(_http.time, "sleep", ...)`). `time` is a single
# process-wide module object, so patching it via this name patches the exact
# same `sleep` `_transport.py` calls - the retry constants below are the same
# objects `_transport.py` uses, not a second copy to drift out of sync with.
import time  # noqa: F401
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from . import exceptions as exc
from ._pagination import check_params
from ._transport import (  # noqa: F401
    MAX_RETRIES,
    MAX_RETRY_AFTER_SECONDS,
    MAX_TOTAL_RETRY_SECONDS,
    Transport,
)


class HttpClient:
    """A thin, synchronous Zendesk HTTP client.

    `transport` is injectable so tests use `httpx.MockTransport` and never touch
    the network - this is the layer `FakeBackend` cannot exercise. Auth, retries
    and the wire itself are `_transport.Transport`'s job (see E8); this class
    validates the path, dispatches to it, and turns the response into a plain
    envelope dict.

    No credential passes through this class at all beyond the `token_provider`
    handed to `Transport` at construction - see `Transport`'s own docstring for
    why no long-lived secret is reachable from either object.
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
        **every** request rather than once here (ADR-009, ADR-015). See
        `Transport.__init__`, which actually holds it, for the full reasoning.

        There is no API-token path. ADR-015 removed it: Zendesk stops issuing
        API tokens on 2026-10-27 and stops honouring them on 2027-04-30, and a
        fallback that silently activates when OAuth is misconfigured turns an
        auth failure into something that reads like a permissions failure -
        which is the confusion the 401 handling in `Transport` goes to some
        trouble to prevent.

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
        self._host = f"{subdomain}.zendesk.com"
        self._base = f"https://{self._host}"
        self._transport = Transport(
            token_provider=token_provider,
            transport=transport,
            timeout=timeout,
            on_invalid_token=on_invalid_token,
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
        self._validate_path(path)
        response = self._transport.send(
            method,
            path,
            host=self._host,
            base=self._base,
            params=sendable,
            json=json,
            idempotent=idempotent,
        )
        return self._envelope(response)

    def post_binary(
        self,
        path: str,
        *,
        content: bytes,
        content_type: str,
        params: Mapping[str, Any] | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        """POST raw bytes. Zendesk's upload endpoint takes a binary body and a
        `filename` query parameter - it is the only call in this library that is
        not JSON, which is why `Transport.send` grew `content`/`content_type`.

        `idempotent` defaults to `False`, unlike `request`'s default of `True` -
        deliberately the opposite way round (Task 4 decision, carried forward
        from Task 1's review). `request`'s callers are all `PUT`s that edit a
        ticket to a target state, so replaying one on a 503 reproduces the same
        state. This client's one binary POST creates a new upload each time it
        is sent: a retried upload does not repeat a no-op, it MINTS A SECOND
        TOKEN - a second file on Zendesk's side, attached to nothing, that
        nothing in the ticket surface will ever show. That is a worse outcome
        than the caller waiting out the 503 and retrying by hand, so this
        method does not retry it automatically.
        """
        self._validate_path(path)
        response = self._transport.send(
            "POST",
            path,
            host=self._host,
            base=self._base,
            params=params,
            content=content,
            content_type=content_type,
            idempotent=idempotent,
        )
        return self._envelope(response)

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
        and `Transport._send` additionally verifies the *built* URL resolves to
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
