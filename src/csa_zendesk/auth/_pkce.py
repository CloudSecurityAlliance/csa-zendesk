"""PKCE, and the URL a browser is sent to.

Pure functions with no I/O, so the interesting property - that the verifier
never leaves this process - is testable directly rather than inferred.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Sequence
from urllib.parse import urlencode


def new_verifier() -> str:
    """A fresh code verifier. 64 bytes of entropy, base64url, 86 characters."""
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()


def challenge_for(verifier: str) -> str:
    """The S256 challenge. Never `plain`: ADR-009 makes S256 mandatory, and a
    `plain` challenge is the verifier itself, which defeats the point."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_url(
    *,
    subdomain: str,
    client_id: str,
    redirect_uri: str,
    scopes: Sequence[str],
    challenge: str,
    state: str,
) -> str:
    """The authorization URL. Takes the *challenge*, never the verifier - the
    verifier is the half that must not travel."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"https://{subdomain}.zendesk.com/oauth/authorizations/new?{query}"
