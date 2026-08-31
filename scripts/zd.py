#!/usr/bin/env python3
"""Minimal Zendesk request helper for probes and experiments.

Deliberately tiny and dependency-free. Returns (status, headers, parsed_body,
raw_text) for EVERY call including failures - the error body is usually the
interesting part, and urllib raises on 4xx/5xx by default, which throws it away.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

SUB = os.environ.get("ZENDESK_SUBDOMAIN", "")


def _require_subdomain() -> str:
    if not SUB:
        raise SystemExit("set ZENDESK_SUBDOMAIN (no default: a hardcoded tenant is "
                         "both a leak and a footgun)")
    return SUB


def _auth() -> str:
    token = os.environ.get("CINO_CSA_ZENDESK", "")
    email = os.environ.get("CINO_CSA_ZENDESK_EMAIL", "")
    missing = [n for n, v in (("CINO_CSA_ZENDESK", token),
                              ("CINO_CSA_ZENDESK_EMAIL", email)) if not v]
    if missing:
        raise SystemExit(f"not set: {', '.join(missing)}")
    return base64.b64encode(f"{email}/token:{token}".encode()).decode()


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(f"https://{_require_subdomain()}.zendesk.com{path}", method=method)
    req.add_header("Authorization", f"Basic {_auth()}")
    req.add_header("Accept", "application/json")
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
