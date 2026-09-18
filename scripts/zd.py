#!/usr/bin/env python3
"""Minimal Zendesk request helper for probes and experiments.

Deliberately tiny and dependency-free. Returns (status, headers, parsed_body,
raw_text) for EVERY call including failures - the error body is usually the
interesting part, and urllib raises on 4xx/5xx by default, which throws it away.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

SUB = os.environ.get("ZENDESK_SUBDOMAIN", "")


def _require_subdomain() -> str:
    if not SUB:
        raise SystemExit("set ZENDESK_SUBDOMAIN (no default: a hardcoded tenant is "
                         "both a leak and a footgun)")
    return SUB


def missing_credentials() -> list[str]:
    """Configuration that is not set. OAuth needs a subdomain and a client id;
    the tokens themselves live in the token file, not the environment."""
    return [n for n in ("CSA_ZENDESK_SUBDOMAIN", "CSA_ZENDESK_MCP_SERVER_IDENTIFIER") if not os.environ.get(n)]


def authorize(req: urllib.request.Request) -> None:
    """Attach credentials to a request. THE auth chokepoint for every script.

    Scripts authenticate exactly as the library does (ADR-015): same token file,
    same refresh, same failure modes. That is the point - the probes are the first
    consumer of this flow, so if it is wrong here we find out before a tool
    depends on it.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from csa_zendesk.auth import access_token

    req.add_header("Authorization", f"Bearer {access_token()}")
    req.add_header("Accept", "application/json")


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(f"https://{_require_subdomain()}.zendesk.com{path}", method=method)
    authorize(req)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            raw, status, hdr = r.read().decode(errors="replace"), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw, status, hdr = e.read().decode(errors="replace"), e.code, dict(e.headers)
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    return status, hdr, parsed, raw
