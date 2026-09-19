"""Talking to Zendesk's OAuth token endpoint. The only module in `auth` that
makes a network request.

This is a public client (API-SURFACE §7): Zendesk issues a `client_secret` to
every OAuth client regardless of type, but a public client is defined by PKCE
replacing that secret, not by sending it alongside PKCE. This module holds no
`client_secret`, reads none from configuration or environment, and never puts
one on the wire - the request body is exactly `grant_type`, `code`,
`client_id`, `redirect_uri`, `scope`, `code_verifier`.

Zendesk has two scope failure modes and they are opposite in shape, so each is
handled at the layer that can actually see it:

  - a scope outside the client's registered ceiling (the live client's ceiling
    is `read tickets:write ticket_attachments:write ticket_views:write`):
    `400 invalid_scope`, no token issued at all. Loud. `_post` surfaces this
    like any other refusal, as `AuthExchangeError`.
  - a scope name Zendesk does not recognise (a typo): the endpoint still
    issues a token, silently narrowed to whatever it did recognise. Silent -
    the token looks valid and every request made with it later 403s, far from
    the cause. `_to_tokens` is the only place that can catch this, by
    comparing what was requested against what the response actually granted.

Task 5 extends this module with `refresh()` and `access_token()`, built on
`_post` and `_to_tokens` below - both are named exactly that and kept usable
from outside `exchange_code` for that reason.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence

import httpx

from .. import exceptions as exc
from . import _store
from ._store import Tokens

__all__ = [
    "exchange_code",
    "refresh",
    "access_token",
    "revoke",
    "ScopeError",
    "AuthExchangeError",
    "NotAuthorised",
    "TokenAlreadyInvalid",
    "RevokeError",
    "REFRESH_MARGIN_SECONDS",
    "MAX_ACCESS_TOKEN_LIFETIME_SECONDS",
    "MAX_REFRESH_TOKEN_LIFETIME_SECONDS",
]


#: Zendesk's documented maximum access-token lifetime - `specs/zendesk-support-oas.yaml`,
#: `CreateTokenForGrantType` (around line 22798): "greater than or equal to 300 seconds
#: (5 minutes) and less than or equal to 172,800 seconds (2 days), or less than
#: `refresh_token_expires_in`, whichever is the shorter." This is the ceiling, not a
#: made-up value: sent as `expires_in` on both `exchange_code` and `refresh` so every
#: grant asks for the longest-lived access token Zendesk will issue. See TODO.md E11 -
#: both tokens live in the same 0600 file, so a short access-token lifetime buys nothing
#: against file theft while costing a refresh (and its own failure modes) every 30
#: minutes instead.
MAX_ACCESS_TOKEN_LIFETIME_SECONDS = 172_800  # 2 days

#: Zendesk's documented maximum refresh-token lifetime - same spec section, same line
#: range: "greater than or equal to 604,800 seconds (7 days) or `expires_in` (if given),
#: and less than or equal to 7,776,000 seconds (90 days)." Sent as `refresh_token_expires_in`
#: on both `exchange_code` and `refresh` - resending it on every refresh matters because
#: Zendesk rotates the refresh token on every use (single-use, confirmed against the live
#: tenant), so re-requesting the maximum on each refresh makes the 90-day window slide
#: forward instead of counting down from the original login.
MAX_REFRESH_TOKEN_LIFETIME_SECONDS = 7_776_000  # 90 days


class ScopeError(exc.ZendeskError):
    """Zendesk issued a token but granted fewer scopes than were requested."""


class AuthExchangeError(exc.ZendeskError):
    """The OAuth token endpoint refused the grant."""


def _post(subdomain: str, body: dict[str, str], transport: httpx.BaseTransport | None) -> dict[str, object]:
    """POST to the token endpoint and return the parsed JSON body.

    On failure, the raised exception carries the HTTP status and nothing else -
    never any part of the response body. `body` (the request we just sent)
    holds the authorization code and the PKCE verifier, or the refresh token;
    Zendesk publishes no stable schema for an OAuth error body, and an
    `error_description` is free text a server can assemble from the request it
    just received - exactly the shape a credential leaks back through. Four
    credentials pass through this module (authorization code, verifier, access
    token, refresh token) and none of them may ever reach an exception message.

    That standard applies one level below the message too, but only for THIS
    frame: `body` is `del`eted from this function's locals before either raise
    below, and before the normal return, so an error tracker that captures
    frame locals (Sentry does by default) does not see it here. This is
    partial, not the whole guarantee the paragraph above might suggest -
    `refresh()`'s own `body` dict and `exchange_code()`'s own `code` and
    `verifier` parameters are separate bindings to the same values in their
    own frames, still live for the life of the call, and a full-stack-capturing
    tracker sees those regardless of what this function does to its own. Closing
    that fully needs credentials passed through as an opaque payload built by a
    helper that closes over nothing, rather than as plain locals in every frame
    that touches them - a design change, deliberately deferred; see TODO.md
    E18.

    A transport failure (`httpx.HTTPError` - offline, DNS, a proxy, a read
    timeout) is translated to `exc.ApiError` naming the OAuth token endpoint,
    never left as a raw httpx exception: a network outage during `auth login`
    or a refresh is not a bug, and letting it escape untyped is exactly the
    hole `cli.py`'s docstring says must not exist. Naming the token endpoint
    specifically also matters when this call happens deep inside `HttpClient`'s
    request hook during a forced refresh (ADR-009) - without a type of its own,
    `_send`'s own translation would catch the raw httpx error instead and blame
    whatever ticket endpoint the caller was actually trying to reach.
    """
    try:
        with httpx.Client(transport=transport, timeout=30.0) as client:
            response = client.post(f"https://{subdomain}.zendesk.com/oauth/tokens", data=body)
    except httpx.HTTPError as e:
        kind = type(e).__name__
        del body
        raise exc.ApiError(f"could not reach the Zendesk OAuth token endpoint ({kind})") from None
    if response.status_code >= 400:
        status = response.status_code
        del body, response
        raise AuthExchangeError(f"Zendesk refused the grant (HTTP {status}).")
    payload = dict(response.json())
    del body, response
    return payload


def _to_tokens(payload: dict[str, object], baseline: Sequence[str]) -> Tokens:
    """Build `Tokens` from a token-endpoint response, after verifying scopes.

    Compares `baseline` against granted scopes rather than trusting a 200: per
    API-SURFACE §7, Zendesk issues a token for an unrecognised scope name
    instead of refusing the grant, so a typo produces a credential that
    authenticates but authorizes nothing - the resulting 403 arrives later, on
    an unrelated call, far from this exchange. Caught here instead.

    `baseline` is a verification floor, not necessarily "what was just
    requested on the wire": `exchange_code` passes the scopes it requested at
    login (the typo case above); `refresh` passes the scopes the credential
    already carried before this call, so a registered-scope ceiling narrowing
    since issuance is caught even though a refresh's own request may ask for
    nothing in particular (see `refresh`'s docstring). Either way the granted
    string on the response - not `baseline` - is what gets persisted on
    `Tokens.scope`, so it always reflects reality rather than an intent.
    """
    granted_str = str(payload.get("scope", ""))
    granted = set(granted_str.split())
    missing = sorted(set(baseline) - granted)
    if missing:
        raise ScopeError(
            f"Zendesk granted {sorted(granted)} but not {missing}. Either a scope "
            f"name Zendesk does not recognise was silently dropped instead of "
            f"refused, or - on a refresh - the client's registered scope ceiling "
            f"narrowed since this credential was issued and it no longer carries "
            f"{missing}. Either way this token will 403 on every call that needs "
            f"{missing}, far from this cause, unless it's reconciled now."
        )
    try:
        return Tokens(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            expires_at=time.time() + float(str(payload["expires_in"])),
            scope=granted_str,
        )
    except (KeyError, ValueError, TypeError) as e:
        # HTTP 200 with a field missing or malformed is still a bug on
        # Zendesk's side of this exchange, not ours - and it must stay inside
        # the ZendeskError hierarchy, per cli.py's "every failure is typed"
        # invariant, rather than escape as a bare KeyError/ValueError.
        raise AuthExchangeError(
            f"Zendesk answered HTTP 200 with a token response missing or malformed "
            f"({type(e).__name__}): expected access_token, refresh_token and expires_in."
        ) from e


def exchange_code(
    *,
    subdomain: str,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    requested_scopes: Sequence[str],
    transport: httpx.BaseTransport | None = None,
) -> Tokens:
    """Exchange an authorization code for tokens.

    Sends no `client_secret` - this is a public client, and PKCE's
    `code_verifier` authenticates the exchange instead. `transport` exists
    only so tests can inject `httpx.MockTransport`; production callers never
    pass it, and this function never touches the network on its own.
    """
    payload = _post(
        subdomain,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "scope": " ".join(requested_scopes),
            "expires_in": str(MAX_ACCESS_TOKEN_LIFETIME_SECONDS),
            "refresh_token_expires_in": str(MAX_REFRESH_TOKEN_LIFETIME_SECONDS),
        },
        transport,
    )
    return _to_tokens(payload, requested_scopes)


#: Refresh this many seconds before the access token actually expires, not after.
#: Token expiration is mandatory and cannot be turned off on the live client, so
#: this is not an optimisation - `access_token()` must always hand back something
#: that will survive the request about to be made with it.
REFRESH_MARGIN_SECONDS = 120


class NotAuthorised(exc.ZendeskError):
    """No usable credential. The operator has to do something."""


def refresh(
    *,
    subdomain: str,
    client_id: str,
    tokens: Tokens,
    transport: httpx.BaseTransport | None = None,
) -> Tokens:
    """Exchange a refresh token for a new access token, and persist the result.

    Sends no `client_secret`, deliberately: we are a public client even on
    refresh, though Zendesk's own refresh-token example shows one. Whether the
    live account accepts a secret-less refresh is unverified; if it turns out
    one is required, adding it back is an architecture decision, not a patch
    here.

    **Sends no `scope` at all, ever.** RFC 6749 §6 is explicit that a refresh
    request MUST NOT ask for scope beyond what the original grant carried, and
    the only scope this module can vouch for as "what the original grant
    carried" is `tokens.scope` - the string Zendesk itself returned at
    issuance, produced by whatever the operator actually consented to in the
    browser. Earlier revisions took a `requested_scopes` parameter here, fed
    from `access_token()` reading `CSA_ZENDESK_SCOPES` - a mutable environment
    variable with no relationship to that consent step. That was wrong in both
    directions at once: widen the env var after login and a refresh would ask
    for (and, since the check below only looks for *missing* scopes, accept
    and persist) more than the browser ever granted; narrow it - for a reason
    that has nothing to do with this credential - and Zendesk would grant
    exactly that narrower request, which the check would then compare against
    the wider `tokens.scope` baseline and refuse as a "registered ceiling
    narrowed" that never actually happened. Omitting `scope` entirely (never
    sending `scope=`, which could itself be read as a request for zero scopes)
    asks Zendesk to keep whatever this credential already carries, per normal
    OAuth refresh semantics - the one request shape that cannot widen or
    narrow anything on its own. `CSA_ZENDESK_SCOPES` is `login()`'s knob, for
    the one browser consent screen a human actually sees; it has no business
    on a refresh request a human never sees at all.

    A refresh response may omit `refresh_token` (Zendesk is not required to
    rotate it every time); `_to_tokens` requires the key to be present, so a
    missing one is filled in from the current `tokens` before that call. Losing
    a rotated refresh token because it was overwritten with an empty value
    would force a full re-login for no reason.

    `_to_tokens`'s scope check runs against `tokens.scope` - what this
    credential already carried - which is what makes it a check at all: with
    nothing ever requested on a refresh, comparing against an empty baseline
    could never fail a subset check against anything. A registered-scope
    ceiling narrowed since issuance must still be caught on refresh even
    though this request never asks for anything in particular.

    **Sends `expires_in` and `refresh_token_expires_in`, both pinned to this
    module's documented maxima, on every refresh.** Rotation means each refresh
    mints a brand-new refresh token; without re-requesting the maximum lifetime
    here, that new token would fall back to Zendesk's 30-day default and the
    90-day window this credential started with would shrink back down on the
    very first refresh. Re-sending the maxima instead makes the 90-day window
    *slide* forward on every use - an operator who runs this server continuously
    (or even just once a quarter) never has to log in again after the first time.
    """
    body: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": tokens.refresh_token,
        "client_id": client_id,
        "expires_in": str(MAX_ACCESS_TOKEN_LIFETIME_SECONDS),
        "refresh_token_expires_in": str(MAX_REFRESH_TOKEN_LIFETIME_SECONDS),
    }
    payload = _post(subdomain, body, transport)
    payload.setdefault("refresh_token", tokens.refresh_token)
    fresh = _to_tokens(payload, tokens.scope.split())
    _store.write(fresh)  # not persisting a refresh means every process refreshes on every start
    return fresh


def access_token(*, transport: httpx.BaseTransport | None = None) -> str:
    """A currently-valid access token. THE accessor every caller uses.

    Called on every request (`HttpClient`'s `token_provider`), so the healthy
    path is a file read and a float comparison - no network at all.
    """
    subdomain = os.environ.get("CSA_ZENDESK_SUBDOMAIN", "")
    if not subdomain:
        raise NotAuthorised(
            "CSA_ZENDESK_SUBDOMAIN is not set. Set it to the Zendesk subdomain this "
            "server talks to (the 'example' in example.zendesk.com). There is no default."
        )
    client_id = os.environ.get("CSA_ZENDESK_MCP_SERVER_IDENTIFIER", "")
    if not client_id:
        raise NotAuthorised(
            "CSA_ZENDESK_MCP_SERVER_IDENTIFIER is not set. Register a public OAuth client in "
            "Zendesk Admin Center (no secret is needed) and set its id. There is no "
            "default client id, deliberately: a shared one would pool every "
            "deployment's rate limit and scope ceiling."
        )
    tokens = _store.read()
    if tokens is None:
        raise NotAuthorised("no token file. Run `csa-zendesk auth login` first.")
    if tokens.expires_at - time.time() > REFRESH_MARGIN_SECONDS:
        return tokens.access_token
    # No CSA_ZENDESK_SCOPES here, deliberately: that variable is login()'s, for
    # the one browser consent screen a human sees. See refresh()'s docstring.
    try:
        fresh = refresh(
            subdomain=subdomain,
            client_id=client_id,
            tokens=tokens,
            transport=transport,
        )
    except AuthExchangeError as e:
        # `_post` deliberately surfaces only the HTTP status (never the response
        # body - the token endpoint's body contains tokens), so this can't say
        # which of the two live possibilities it was: Zendesk refusing the grant
        # outright, or the refresh token having simply expired (30 days by
        # default, with expiration mandatory and not something we can turn off).
        # Say both, and the fix is the same either way, so a bare "unauthorised"
        # doesn't send an operator chasing a permissions problem instead.
        raise NotAuthorised(
            "Zendesk refused to refresh this token. Either the refresh token has "
            "expired or was revoked (Zendesk's default lifetime is 30 days of "
            "inactivity), or Zendesk refused the refresh grant itself - the "
            "response does not say which. Run `csa-zendesk auth login` again to "
            "get a new token."
        ) from e
    return fresh.access_token


class TokenAlreadyInvalid(exc.ZendeskError):
    """Revocation was refused because the token is already invalid or expired
    (HTTP 401 on the revoke call itself). The credential is already dead, so
    clearing the local file is safe - there is nothing left to protect."""


class RevokeError(exc.ZendeskError):
    """Revocation failed for a reason other than the token already being
    invalid - a network failure, a 5xx, or some other refusal.

    The credential may still be live server-side. Callers must NOT clear the
    local token file on this exception: doing so would delete the one thing
    that could still revoke it, and leave a possibly-stolen credential both
    live and un-revocable by this tool.
    """


def revoke(*, subdomain: str, tokens: Tokens, transport: httpx.BaseTransport | None = None) -> None:
    """Revoke `tokens.access_token` server-side via `DELETE
    /api/v2/oauth/tokens/current` (spec: `RevokeCurrentOAuthToken`, around line
    9712), authenticated by `Authorization: Bearer <the token being revoked>` -
    the same shape the spec documents, and the only one it documents; there is
    no separate "revoke this refresh token" endpoint.

    **Whether this also invalidates the paired refresh token is unknown.** The
    spec says only that it revokes "the current OAuth token" and returns `204
    No Content`; it documents nothing about the refresh token issued alongside
    it, and this module does not guess. See TODO.md E20 for the live check
    that would settle it - deliberately not run here (no network in tests, and
    Kurt asked that nothing run against the live API for this change).

    Callers distinguish two failure shapes, because they call for opposite
    handling of the local token file:

    - `TokenAlreadyInvalid` (HTTP 401 on this call): the access token was
      already invalid or expired, so there is nothing left to revoke. Safe to
      treat as success and clear the local file.
    - `RevokeError` (anything else >= 400) or `exc.ApiError` (the request
      never reached Zendesk at all): the token may still be live. The local
      file must be left in place, so the operator still holds the one
      credential that can revoke it - by retrying, or by hand in Zendesk
      Admin Center (Apps and integrations > APIs > OAuth clients).

    A transport failure is translated to `exc.ApiError`, same as `_post` and
    `whoami` - never a raw `httpx` exception.
    """
    headers = {"Authorization": f"Bearer {tokens.access_token}"}
    try:
        with httpx.Client(transport=transport, timeout=30.0) as client:
            response = client.delete(
                f"https://{subdomain}.zendesk.com/api/v2/oauth/tokens/current",
                headers=headers,
            )
    except httpx.HTTPError as e:
        kind = type(e).__name__
        del headers
        raise exc.ApiError(f"could not reach the Zendesk OAuth revoke endpoint ({kind})") from None
    status = response.status_code
    del headers, response  # never let the bearer token linger in this frame past this point
    if status == 401:
        raise TokenAlreadyInvalid(
            "Zendesk says this access token is already invalid or expired (HTTP 401) - nothing left to revoke."
        )
    if status >= 400:
        raise RevokeError(f"Zendesk refused to revoke the token (HTTP {status}).")
