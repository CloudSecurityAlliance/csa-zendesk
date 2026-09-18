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

import time
from collections.abc import Sequence

import httpx

from .. import exceptions as exc
from ._store import Tokens

__all__ = ["exchange_code", "ScopeError", "AuthExchangeError"]


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
