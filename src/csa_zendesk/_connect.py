"""`connect()` - the join between the OAuth layer and the gated client.

The two halves it wires together were built and tested independently and had
never been introduced to each other before this module: `auth` (OAuth,
`access_token`/`clear`/token storage) and the library proper (`HttpClient`,
`ApiBackend`, `PolicyBackend`, `Policy`, `ZendeskClient`). Everything below is
the specific, sanctioned way to connect them - nowhere else in the package
should duplicate this wiring.

Private module, public function: this file is named `_connect.py`, not
`connect.py`, so that `__init__.py`'s `from ._connect import connect` cannot
shadow a same-named submodule as a package attribute the way `auth/__init__.py`
already does with `from .whoami import whoami` (`auth.whoami.NotAuthenticated`
raises `AttributeError` there - a known, deferred wart). Matches this
package's existing convention of a leading underscore for a private
implementation module behind a public surface (`_http.py`, `_errors.py`,
`_pagination.py`, `_scope.py`).
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
    (an explicit set) is required - both, or neither, is a caller error.
    Nothing here defaults to a granted set of capabilities: this project
    refuses defaults for anything security-relevant, deliberately and
    repeatedly (`CSA_ZENDESK_SUBDOMAIN` and the OAuth client id have no
    default, `policy._GATES` fails closed rather than leaving a method
    ungoverned), and a factory that silently picked *some* profile for a
    caller who named none would hand out working authority nobody asked for,
    with nothing to say so. A caller who forgets the argument gets told, not
    granted a policy.

    `transport` exists only so tests can inject `httpx.MockTransport`;
    production callers never pass it.
    """
    if profile is not None and capabilities is not None:
        raise ValueError("connect() requires exactly one of `profile` or `capabilities`; both were given.")
    if profile is not None:
        policy = Policy.from_profile(profile)
    elif capabilities is not None:
        policy = Policy(capabilities)
    else:
        raise ValueError(
            "connect() requires exactly one of `profile` or `capabilities`; neither was given. "
            "There is no default policy - a caller must say what authority to grant."
        )

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

    def _force_fresh_token() -> None:
        """React to Zendesk's `invalid_token` 401 by forcing a real refresh.

        Corrected in the final whole-branch review's fix wave: this used to be
        `auth.clear`, which was wrong. `auth.clear` is `_store.clear`, and it
        unlinks the WHOLE token file - access token AND refresh token, since
        both live in it. After that, the retry's `token_provider()` call
        (`auth.access_token`) finds no file at all and raises `NotAuthorised`,
        so the retry could never succeed - and a valid 90-day refresh
        credential was destroyed to find that out, as a side effect of a tool
        annotated `read_only_hint=True, destructive_hint=False`. See TODO.md E12.

        `auth.access_token(force=True, ...)` is the actual fix: it skips the
        `REFRESH_MARGIN_SECONDS` freshness check (which would otherwise hand
        back the SAME apparently-unexpired token that was just rejected) and
        refreshes unconditionally using the stored refresh token. `refresh()`
        persists the new pair via `_store.write()` before returning, so this
        function discards `access_token`'s return value on purpose - the
        point is the side effect on disk, not the value - and the retry's own
        `token_provider()` call picks the fresh access token straight back up
        from that file.
        """
        auth.access_token(force=True, transport=transport)

    http = HttpClient(
        subdomain=subdomain,
        token_provider=auth.access_token,
        on_invalid_token=_force_fresh_token,
        transport=transport,
    )
    backend = ApiBackend(http)
    return ZendeskClient(PolicyBackend(backend, policy))
