#!/usr/bin/env python3
"""Read-only availability probe: which Zendesk families this account can reach.

GET only. Records status, the top-level JSON keys, and a COUNT - never rows.
Ticket bodies are customer correspondence; this script must never write one to
disk. See .gitignore.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUB = os.environ.get("ZENDESK_SUBDOMAIN", "")
EMAIL = os.environ.get("CINO_CSA_ZENDESK_EMAIL", "")
TOKEN = os.environ.get("CINO_CSA_ZENDESK", "")

# capability, family, path. One cheap representative GET each.
PROBES = [
    ("ticketing", "Tickets",              "/api/v2/tickets.json?page[size]=1"),
    ("ticketing", "Ticket Audits",        "/api/v2/ticket_audits.json?page[size]=1"),
    ("ticketing", "Suspended Tickets",    "/api/v2/suspended_tickets.json?page[size]=1"),
    ("ticketing", "Users",                "/api/v2/users.json?page[size]=1"),
    ("ticketing", "Organizations",        "/api/v2/organizations.json?page[size]=1"),
    ("ticketing", "Groups",               "/api/v2/groups.json?page[size]=1"),
    ("ticketing", "Views",                "/api/v2/views.json?page[size]=1"),
    ("ticketing", "Macros",               "/api/v2/macros.json?page[size]=1"),
    ("ticketing", "Triggers",             "/api/v2/triggers.json?page[size]=1"),
    ("ticketing", "Automations",          "/api/v2/automations.json?page[size]=1"),
    ("ticketing", "Ticket Fields",        "/api/v2/ticket_fields.json"),
    ("ticketing", "Ticket Forms",         "/api/v2/ticket_forms.json"),
    ("ticketing", "Custom Ticket Statuses","/api/v2/custom_statuses.json"),
    ("ticketing", "Brands",               "/api/v2/brands.json"),
    ("ticketing", "SLA Policies",         "/api/v2/slas/policies.json"),
    ("ticketing", "Satisfaction Ratings", "/api/v2/satisfaction_ratings.json?page[size]=1"),
    ("ticketing", "Search",               "/api/v2/search.json?query=type:ticket&page[size]=1"),
    ("ticketing", "Search Count",         "/api/v2/search/count.json?query=type:ticket"),
    ("ticketing", "Job Statuses",         "/api/v2/job_statuses.json?page[size]=1"),
    ("ticketing", "Webhooks",             "/api/v2/webhooks"),
    ("ticketing", "Targets",              "/api/v2/targets.json"),
    ("ticketing", "Custom Objects",       "/api/v2/custom_objects"),
    ("ticketing", "Custom Roles",         "/api/v2/custom_roles.json"),
    ("ticketing", "Dynamic Content",      "/api/v2/dynamic_content/items.json"),
    ("ticketing", "Audit Logs",           "/api/v2/audit_logs.json?page[size]=1"),
    ("ticketing", "Tags",                 "/api/v2/tags.json"),
    ("ticketing", "Account Settings",     "/api/v2/account/settings.json"),
    ("ticketing", "Sharing Agreements",   "/api/v2/sharing_agreements.json"),
    ("ticketing", "Resource Collections", "/api/v2/resource_collections.json"),
    ("ticketing", "Trigger Categories",   "/api/v2/trigger_categories"),
    ("ticketing", "Task Lists",           "/api/v2/task_lists"),
    ("ticketing", "ITAM Assets",          "/api/v2/assets"),
    ("ticketing", "Omnichannel Queues",   "/api/v2/queues"),
    ("ticketing", "Deletion Schedules",   "/api/v2/deletion_schedules"),
    ("ticketing", "Bookmarks",            "/api/v2/bookmarks"),
    ("ticketing", "Skill Based Routing",  "/api/v2/routing/attributes"),
    ("ticketing", "Incremental Tickets",  "/api/v2/incremental/tickets/cursor.json?start_time=1"),
    ("help_center","Articles",            "/api/v2/help_center/articles.json?page[size]=1"),
    ("help_center","Sections",            "/api/v2/help_center/sections.json?page[size]=1"),
    ("help_center","Categories",          "/api/v2/help_center/categories.json?page[size]=1"),
    ("help_center","Article Comments",    "/api/v2/help_center/community/posts.json?page[size]=1"),
    ("help_center","User Segments",       "/api/v2/help_center/user_segments.json"),
    ("help_center","Article Labels",      "/api/v2/help_center/articles/labels.json"),
    ("help_center","HC Search",           "/api/v2/help_center/articles/search.json?query=cloud"),
    ("voice",     "Talk Phone Numbers",   "/api/v2/channels/voice/phone_numbers.json"),
    ("voice",     "Talk Stats",           "/api/v2/channels/voice/stats/current_queue_activity.json"),
    ("voice",     "Talk Availability",    "/api/v2/channels/voice/availabilities.json"),
    ("other",     "Chat (live chat)",     "/api/v2/chats"),
    ("other",     "Apps installed",       "/api/v2/apps/installations.json"),
]


def probe(path: str) -> dict:
    url = f"https://{SUB}.zendesk.com{path}"
    req = urllib.request.Request(url, method="GET")
    auth = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    import base64
    raw = base64.b64encode(f"{EMAIL}/token:{TOKEN}".encode()).decode()
    req.add_header("Authorization", f"Basic {raw}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            hdr = dict(r.headers)
            status = r.status
    except urllib.error.HTTPError as e:
        body = e.read(); hdr = dict(e.headers); status = e.code
    except Exception as e:
        return {"status": 0, "note": type(e).__name__}

    out = {"status": status,
           "ratelimit": hdr.get("x-rate-limit-remaining", ""),
           "endpoint_bucket": next((v for k, v in hdr.items()
                                    if k.lower().startswith("zendesk-ratelimit-")), "")}
    try:
        doc = json.loads(body)
    except ValueError:
        out["shape"] = "NOT-JSON"
        return out
    if not isinstance(doc, dict):
        out["shape"] = f"JSON-{type(doc).__name__}"
        return out
    out["keys"] = ",".join(sorted(doc)[:8])
    # count without retaining data
    for k, v in doc.items():
        if isinstance(v, list):
            out["n_in_page"] = len(v); out["collection"] = k; break
    for k in ("count", "total_count"):
        if isinstance(doc.get(k), int):
            out["count"] = doc[k]
    meta = doc.get("meta") or {}
    if isinstance(meta, dict) and "has_more" in meta:
        out["has_more"] = meta["has_more"]
    if "next_page" in doc:
        out["next_page"] = "set" if doc["next_page"] else "null"
    if "error" in doc or "errors" in doc:
        e = doc.get("error") or doc.get("errors")
        out["error"] = (json.dumps(e)[:90])
    return out


def main() -> int:
    missing = [n for n, v in (("CINO_CSA_ZENDESK", TOKEN),
                              ("CINO_CSA_ZENDESK_EMAIL", EMAIL)) if not v]
    if missing:
        # Named individually: "credentials not set" sends someone hunting for the
        # wrong one. Both live in ./.env; neither has a default worth guessing.
        print(f"not set: {', '.join(missing)}", file=sys.stderr); return 1
    results = []
    for cap, family, path in PROBES:
        r = probe(path)
        r.update(capability=cap, family=family, path=path.split("?")[0])
        results.append(r)
        flag = "ok " if r["status"] == 200 else "!! "
        print(f"{flag}{r['status']:3d}  {cap:12s} {family:22s} "
              f"n={r.get('n_in_page','-'):>4} count={r.get('count','-'):>7} "
              f"has_more={r.get('has_more','-')} next_page={r.get('next_page','-')} "
              f"{r.get('error','')}")
        time.sleep(0.15)
    (ROOT / "tenant-config/family-probe.json").write_text(json.dumps(results, indent=2))
    print(f"\n-> tenant-config/family-probe.json  ({sum(1 for r in results if r['status']==200)}"
          f"/{len(results)} reachable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
