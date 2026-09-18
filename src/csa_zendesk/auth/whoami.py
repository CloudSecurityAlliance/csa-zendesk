"""Who the credential actually is.

`users/me.json` answers **HTTP 200 with an "Anonymous user" object** when the
caller is wholly unauthenticated (project invariant 1, `analysis/API-SURFACE.md`
§5.1), so a status-code check is not an identity check - a naive whoami would
report success for a credential that authenticates nothing. This asserts on the
response body instead, and raises `NotAuthenticated` when it looks anonymous.
"""

from __future__ import annotations

import os

import httpx

from .. import exceptions as exc
from ._flow import access_token

__all__ = ["NotAuthenticated", "whoami"]


class NotAuthenticated(exc.ZendeskError):
    """The credential reached Zendesk and Zendesk did not recognise it.

    Distinct from `_flow.NotAuthorised`: that one fires before any request is
    made (no token file, no env var). This one fires *after* a request that
    came back HTTP 200 - the credential was sent, and Zendesk answered with
    the anonymous-user object it hands back to nobody-in-particular.
    """


def whoami(*, subdomain: str | None = None, transport: httpx.BaseTransport | None = None) -> dict[str, object]:
    """The identity `access_token()`'s credential actually resolves to.

    `subdomain` defaults to `CSA_ZENDESK_SUBDOMAIN`, same as every other entry
    point - `access_token()` itself also reads that variable independently
    (it takes no subdomain parameter), so the two must agree, which is
    guaranteed as long as both are left to read the same environment rather
    than being passed conflicting values.

    `transport` reaches only the `users/me.json` request here, never
    `access_token()`'s own possible refresh call - tests must hand `whoami` an
    already-fresh cached token (see `tests/auth/test_whoami.py`) so that
    refresh path is never exercised under a mock, per the "no network in
    tests" rule.
    """
    sub = subdomain or os.environ.get("CSA_ZENDESK_SUBDOMAIN", "")
    with httpx.Client(transport=transport, timeout=30.0) as client:
        response = client.get(
            f"https://{sub}.zendesk.com/api/v2/users/me.json",
            headers={"Authorization": f"Bearer {access_token()}", "Accept": "application/json"},
        )
    body = dict(response.json())
    raw_user = body.get("user")
    user: dict[str, object] = dict(raw_user) if isinstance(raw_user, dict) else {}
    if user.get("id") is None or user.get("name") == "Anonymous user":
        raise NotAuthenticated(
            "Zendesk answered with an Anonymous user object, which is what it "
            "returns for an unauthenticated request even at HTTP 200. The token is "
            "not being sent or is not recognised. Run `csa-zendesk auth login`."
        )
    return user
