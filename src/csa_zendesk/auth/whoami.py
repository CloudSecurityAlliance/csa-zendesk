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
    came back HTTP 200 or an explicit 401/403 - the credential was sent, and
    Zendesk either answered with the anonymous-user object it hands back to
    nobody-in-particular, or rejected the credential outright.
    """


def whoami(*, transport: httpx.BaseTransport | None = None) -> dict[str, object]:
    """The identity `access_token()`'s credential actually resolves to.

    Always reads `CSA_ZENDESK_SUBDOMAIN` directly, the same way `access_token()`
    does - there is deliberately no `subdomain` parameter to override it with.
    This is public surface (`auth.__all__`) that attaches a live bearer token
    to whatever host it builds; a caller-supplied subdomain interpolated
    straight into the request URL (`subdomain="evil.com/"` builds host
    `evil.com`; `"x.attacker.net"` builds `x.attacker.net.zendesk.com`) would
    send that token to an arbitrary host of the caller's choosing. `_http.py`'s
    `_send` guards exactly this class of thing for the ticket API by comparing
    the built request's host against the expected one; `whoami` closes the
    same hole the simpler way, by never accepting a host input in the first
    place - the token is only ever valid for the tenant it was issued against,
    so there is no legitimate use for pointing it anywhere else.

    `transport` reaches only the `users/me.json` request here, never
    `access_token()`'s own possible refresh call - tests must hand `whoami` an
    already-fresh cached token (see `tests/auth/test_whoami.py`) so that
    refresh path is never exercised under a mock, per the "no network in
    tests" rule.

    Every failure stays inside the `ZendeskError` hierarchy (`cli.py`'s
    invariant): a transport failure (offline, DNS, a proxy, a timeout) becomes
    `exc.ApiError` rather than a raw `httpx` exception; a `401`/`403` becomes
    `NotAuthenticated`, since the credential really was rejected; any other
    non-2xx (429 rate-limited, 503 maintenance, or anything else) becomes
    `exc.ApiError` too, but says Zendesk could not be reached or is
    rate-limiting - NOT that the operator should re-authenticate, which would
    be false and would send them to mint a new credential for a problem that
    has nothing to do with credentials. `_cmd_login` runs this right after a
    successful login; misreporting a 429 here as "not authenticated" would
    report a login that worked as a failure.
    """
    sub = os.environ.get("CSA_ZENDESK_SUBDOMAIN", "")
    try:
        with httpx.Client(transport=transport, timeout=30.0) as client:
            response = client.get(
                f"https://{sub}.zendesk.com/api/v2/users/me.json",
                headers={"Authorization": f"Bearer {access_token()}", "Accept": "application/json"},
            )
    except httpx.HTTPError as e:
        kind = type(e).__name__
        raise exc.ApiError(f"could not reach Zendesk ({kind}) requesting GET /api/v2/users/me.json") from None
    if response.status_code in (401, 403):
        raise NotAuthenticated(
            f"Zendesk rejected the credential (HTTP {response.status_code}). Run `csa-zendesk auth login`."
        )
    if response.status_code >= 400:
        raise exc.ApiError(
            f"Zendesk returned HTTP {response.status_code}, not a credential problem - it could not be "
            f"reached cleanly or is rate-limiting. Retry shortly; this is not a reason to re-authenticate.",
            status=response.status_code,
        )
    try:
        body = dict(response.json())
    except ValueError as e:
        raise exc.ApiError(
            f"Zendesk returned HTTP {response.status_code} with a body that is not JSON",
            status=response.status_code,
        ) from e
    raw_user = body.get("user")
    user: dict[str, object] = dict(raw_user) if isinstance(raw_user, dict) else {}
    if user.get("id") is None or user.get("name") == "Anonymous user":
        raise NotAuthenticated(
            "Zendesk answered with an Anonymous user object, which is what it "
            "returns for an unauthenticated request even at HTTP 200. The token is "
            "not being sent or is not recognised. Run `csa-zendesk auth login`."
        )
    return user
