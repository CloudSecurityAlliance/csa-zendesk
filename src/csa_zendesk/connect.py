"""`connect()` - the join between the OAuth layer and the gated client.

The two halves it wires together were built and tested independently and had
never been introduced to each other before this module: `auth` (OAuth,
`access_token`/`clear`/token storage) and the library proper (`HttpClient`,
`ApiBackend`, `PolicyBackend`, `Policy`, `ZendeskClient`). Everything below is
the specific, sanctioned way to connect them - nowhere else in the package
should duplicate this wiring.
"""

from __future__ import annotations

import os

import httpx

from . import auth
from ._http import HttpClient
from .backend import ApiBackend
from .client import ZendeskClient
from .policy import Policy, PolicyBackend


def connect(
    *,
    profile: str | None = None,
    capabilities: frozenset[str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> ZendeskClient:
    """Build a policy-gated `ZendeskClient` wired to the OAuth layer.

    Exactly one of `profile` (a name from `policy.PROFILES`) or `capabilities`
    (an explicit set) may be given; passing both is a caller error, not a
    "last one wins" merge - there would be no honest way to say which one the
    caller actually meant. Passing neither resolves to the `"default"`
    profile: everything reversible, nothing that reaches outward or deletes -
    the same posture `policy.PROFILES["default"]`'s own comment describes as
    the sane thing to grant when nobody has said otherwise.

    `transport` exists only so tests can inject `httpx.MockTransport`;
    production callers never pass it.
    """
    if profile is not None and capabilities is not None:
        raise ValueError("connect() takes a profile or capabilities, not both.")
    if profile is not None:
        policy = Policy.from_profile(profile)
    elif capabilities is not None:
        policy = Policy(capabilities)
    else:
        policy = Policy.from_profile("default")

    subdomain = os.environ.get("CSA_ZENDESK_SUBDOMAIN", "")
    if not subdomain:
        # HttpClient's own constructor would refuse an empty subdomain too, but
        # with a bare ValueError - and reading it here, before HttpClient is
        # ever constructed, is what lets this raise the same typed, operator-
        # facing error auth.access_token() raises for the identical variable,
        # rather than a KeyError or an unrelated ValueError surfacing first.
        raise auth.NotAuthorised(
            "CSA_ZENDESK_SUBDOMAIN is not set. Set it to the Zendesk subdomain this "
            "server talks to (the 'example' in example.zendesk.com). There is no default."
        )

    http = HttpClient(
        subdomain=subdomain,
        token_provider=auth.access_token,
        # TODO E12: on_invalid_token must FORCE a new token, and wiring it to
        # auth.access_token would not do that. access_token() only refreshes
        # proactively when the stored token is within its 120-second
        # REFRESH_MARGIN_SECONDS of expiry, so a retry immediately after a 401
        # would call access_token(), get back the SAME still-"unexpired"
        # token, hit the SAME 401, and burn a request for nothing. auth.clear
        # is the sanctioned wiring instead: it discards the rejected token
        # outright, so the next access_token() call has nothing left to reuse
        # and must obtain a genuinely fresh one.
        on_invalid_token=auth.clear,
        transport=transport,
    )
    backend = ApiBackend(http)
    return ZendeskClient(PolicyBackend(backend, policy))
