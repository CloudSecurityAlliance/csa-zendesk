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
    "ScopeError",
    "AuthExchangeError",
    "NotAuthorised",
    "REFRESH_MARGIN_SECONDS",
]


class ScopeError(exc.ZendeskError):
    """Zendesk issued a token but granted fewer scopes than were requested."""


class AuthExchangeError(exc.ZendeskError):
    """The OAuth token endpoint refused the grant."""


def _post(subdomain: str, body: dict[str, str], transport: httpx.BaseTransport | None) -> dict[str, object]:
    """POST to the token endpoint and return the parsed JSON body.

    On failure, the raised exception carries the HTTP status and nothing else -
    never any part of the response body. `body` (the request we just sent)
    holds the authorization code and the PKCE verifier; Zendesk publishes no
    stable schema for an OAuth error body, and an `error_description` is free
    text a server can assemble from the request it just received - exactly the
    shape a credential leaks back through. Four credentials pass through this
    module (authorization code, verifier, access token, refresh token) and
    none of them may ever reach an exception message.
    """
    with httpx.Client(transport=transport, timeout=30.0) as client:
        response = client.post(f"https://{subdomain}.zendesk.com/oauth/tokens", data=body)
    if response.status_code >= 400:
        raise AuthExchangeError(f"Zendesk refused the grant (HTTP {response.status_code}).")
    return dict(response.json())


def _to_tokens(payload: dict[str, object], requested: Sequence[str]) -> Tokens:
    """Build `Tokens` from a token-endpoint response, after verifying scopes.

    Compares requested scopes against granted scopes rather than trusting a
    200: per API-SURFACE §7, Zendesk issues a token for an unrecognised scope
    name instead of refusing the grant, so a typo produces a credential that
    authenticates but authorizes nothing - the resulting 403 arrives later, on
    an unrelated call, far from this exchange. Caught here instead.
    """
    granted = set(str(payload.get("scope", "")).split())
    missing = sorted(set(requested) - granted)
    if missing:
        raise ScopeError(
            f"Zendesk granted {sorted(granted)} but not {missing}. A scope name Zendesk "
            f"does not recognise is silently dropped rather than refused, so check "
            f"{missing} against the client's registered scopes before retrying - the "
            f"token above will otherwise look valid and 403 on every call that needs it."
        )
    return Tokens(
        access_token=str(payload["access_token"]),
        refresh_token=str(payload["refresh_token"]),
        expires_at=time.time() + float(str(payload["expires_in"])),
    )


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
    requested_scopes: Sequence[str],
    transport: httpx.BaseTransport | None = None,
) -> Tokens:
    """Exchange a refresh token for a new access token, and persist the result.

    Sends no `client_secret`, deliberately: we are a public client even on
    refresh, though Zendesk's own refresh-token example shows one. Whether the
    live account accepts a secret-less refresh is unverified; if it turns out
    one is required, adding it back is an architecture decision, not a patch
    here.

    `requested_scopes` is sent as `scope` only when non-empty. Omitting it
    entirely (rather than sending an empty string) asks Zendesk to keep
    whatever scopes were already granted, per normal OAuth refresh semantics -
    sending `scope=` outright could instead be read as a request for zero
    scopes.

    A refresh response may omit `refresh_token` (Zendesk is not required to
    rotate it every time); `_to_tokens` requires the key to be present, so a
    missing one is filled in from the current `tokens` before that call. Losing
    a rotated refresh token because it was overwritten with an empty value
    would force a full re-login for no reason.
    """
    body: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": tokens.refresh_token,
        "client_id": client_id,
    }
    if requested_scopes:
        body["scope"] = " ".join(requested_scopes)
    payload = _post(subdomain, body, transport)
    payload.setdefault("refresh_token", tokens.refresh_token)
    fresh = _to_tokens(payload, requested_scopes)
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
    scopes = tuple(sorted(set(os.environ.get("CSA_ZENDESK_SCOPES", "").split())))
    try:
        fresh = refresh(
            subdomain=subdomain,
            client_id=client_id,
            tokens=tokens,
            requested_scopes=scopes,
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
