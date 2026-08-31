#!/usr/bin/env python3
"""Pull the web UI's own action and query vocabulary from Zendesk.

Macros, triggers and views are how the Zendesk agent interface packages what a
person can DO to a ticket and what they can FILTER on. Zendesk publishes those
definitions through the API, computed against *this account* - so they enumerate
the real vocabulary including custom fields, rather than a generic reading of the
docs.

This is the bridge between "882 API operations" and "the actions a user recognises".

PII: assignee, follower and group choice lists contain real people. This script
records CHOICE COUNTS ONLY for any list whose subject is a person or group, never
the names. See .gitignore.
"""
from __future__ import annotations

import base64
import json
import re
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUB = os.environ.get("ZENDESK_SUBDOMAIN", "")
TOKEN = os.environ.get("CINO_CSA_ZENDESK", "")
EMAIL = os.environ.get("CINO_CSA_ZENDESK_EMAIL", "")

# Subjects whose choice lists name real people or internal groups. Counts only.
PEOPLE = {"assignee_id", "follower", "group_id", "requester_id", "submitter_id",
          "current_user_id", "role", "organization_id"}

# A subject-name allowlist is not sufficient and was not: gating on the SUBJECT let 80
# internal routing addresses through under `Received at`, a subject nobody thought to
# list. Withholding is therefore decided by the VALUE - anything that looks like an
# address or a person - so a subject we failed to anticipate cannot leak.
_ADDRESSY = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

ENDPOINTS = {
    "macro_actions":   ("/api/v2/macros/definitions.json",   ("definitions", "actions")),
    "trigger_actions": ("/api/v2/triggers/definitions.json", ("definitions", "actions")),
    "trigger_conditions": ("/api/v2/triggers/definitions.json", ("definitions", "conditions_all")),
    "view_conditions": ("/api/v2/views/definitions.json",    ("definitions", "conditions_all")),
    "view_output":     ("/api/v2/views/definitions.json",    ("definitions", "output")),
}


def get(path: str) -> dict:
    req = urllib.request.Request(f"https://{SUB}.zendesk.com{path}", method="GET")
    raw = base64.b64encode(f"{EMAIL}/token:{TOKEN}".encode()).decode()
    req.add_header("Authorization", f"Basic {raw}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def dig(doc: dict, path: tuple[str, ...]):
    for key in path:
        if not isinstance(doc, dict) or key not in doc:
            # The envelope key differs per endpoint and has changed before; say so
            # rather than returning [] and reporting "no actions defined".
            raise KeyError(f"expected key {key!r}, got {list(doc)[:6] if isinstance(doc, dict) else type(doc).__name__}")
        doc = doc[key]
    return doc


def scrub(item: dict) -> dict:
    out = {"title": item.get("title", ""), "subject": item.get("subject", ""),
           "type": item.get("type", "")}
    vals = item.get("values")
    if isinstance(vals, list) and vals:
        out["n_choices"] = len(vals)
        subj = out["subject"]
        titles = [str(v.get("title", v.get("value", ""))) for v in vals]
        by_subject = subj in PEOPLE or (subj.endswith("_id")
                                        and subj not in ("ticket_form_id", "brand_id"))
        by_value = any(_ADDRESSY.search(t) for t in titles)
        if by_subject or by_value:
            out["choices"] = "<withheld: identifies people, groups or addresses>"
        else:
            out["choices"] = titles[:40]
    return out


def main() -> int:
    missing = [n for n, v in (("CINO_CSA_ZENDESK", TOKEN),
                              ("CINO_CSA_ZENDESK_EMAIL", EMAIL)) if not v]
    if missing:
        print(f"not set: {', '.join(missing)}", file=sys.stderr); return 1

    cache: dict[str, dict] = {}
    out: dict[str, list] = {}
    for name, (path, keys) in ENDPOINTS.items():
        if path not in cache:
            cache[path] = get(path)
        out[name] = [scrub(i) for i in dig(cache[path], keys)]
        print(f"  {len(out[name]):3d}  {name}")

    dest = ROOT / "tenant-config/ui-action-vocabulary.json"
    dest.write_text(json.dumps(out, indent=1))
    print(f"\n-> {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
