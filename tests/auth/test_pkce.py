import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

from csa_zendesk.auth import _pkce


def test_the_verifier_is_long_enough_and_url_safe():
    # RFC 7636 §4.1: 43-128 characters from the unreserved set.
    v = _pkce.new_verifier()
    assert 43 <= len(v) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", v)


def test_two_verifiers_are_never_the_same():
    assert _pkce.new_verifier() != _pkce.new_verifier()


def test_the_challenge_is_s256_not_plain():
    # ADR-009: PKCE (S256) is mandatory. A "plain" challenge would equal the
    # verifier, which is the degenerate case this asserts against.
    v = "a" * 43
    expected = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert _pkce.challenge_for(v) == expected
    assert _pkce.challenge_for(v) != v


def test_the_authorize_url_carries_every_required_parameter():
    url = _pkce.authorize_url(
        subdomain="example",
        client_id="cid",
        redirect_uri="http://localhost:1234/callback",
        scopes=["tickets:read", "hc:read"],
        challenge="chal",
        state="st",
    )
    parsed = urlparse(url)
    assert parsed.netloc == "example.zendesk.com"
    assert parsed.path == "/oauth/authorizations/new"
    q = parse_qs(parsed.query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["cid"]
    assert q["code_challenge"] == ["chal"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["st"]
    assert q["scope"] == ["tickets:read hc:read"]
    assert q["redirect_uri"] == ["http://localhost:1234/callback"]
    assert "client_secret" not in q


def test_no_verifier_ever_reaches_the_url():
    v = _pkce.new_verifier()
    url = _pkce.authorize_url(
        subdomain="example",
        client_id="cid",
        redirect_uri="http://localhost:1/cb",
        scopes=["tickets:read"],
        challenge=_pkce.challenge_for(v),
        state="st",
    )
    assert v not in url


def test_redirect_uri_percent_encoding_survives_round_trip():
    # Stress test: redirect_uri with special characters (+ : / ?) that could be
    # mangled if something re-parses or normalises the URL. Zendesk string-matches
    # the exact URI against a pre-registered list, so any normalisation breaks auth.
    redirect_with_query = "http://localhost:8765/callback?state=abc+def:ghi"
    url = _pkce.authorize_url(
        subdomain="example",
        client_id="cid",
        redirect_uri=redirect_with_query,
        scopes=["tickets:read"],
        challenge="chal",
        state="st",
    )
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert q["redirect_uri"] == [redirect_with_query]
