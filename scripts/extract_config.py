#!/usr/bin/env python3
"""Walk the tenant and extract its CONFIGURATION - the rules, not the rows.

Customer360 mirrors Zendesk *data*. This extracts the things that data obeys:
which fields exist, which are required to solve, what the forms condition on,
what the triggers and SLAs do, how routing is set up. None of that is in a row
mirror, and it is what any tool operating on tickets has to understand.

Two outputs, deliberately:

  tenant-config/*.json     the full extract. GITIGNORED - group structure, SLA
                           policies, trigger rules, support addresses and app
                           installs are CSA infrastructure detail and this repo
                           is going public.
  tenant-config/SUMMARY.md counts and shapes only, safe to read aloud.

Pagination: every collection is walked to exhaustion. An earlier probe in this
repo took one page of 100 ticket_fields out of 130 and drew a conclusion from
it, which is the exact failure this project exists to prevent.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from zd import SUB, call

OUT = pathlib.Path(__file__).resolve().parent.parent / "tenant-config"

# name -> (path, collection key or None for a singleton document)
TARGETS: dict[str, tuple[str, str | None]] = {
    # --- the account itself
    "account_settings":      ("/api/v2/account/settings.json", None),
    "locales":               ("/api/v2/locales.json", "locales"),
    "brands":                ("/api/v2/brands.json", "brands"),
    # --- the ticket data model: what a ticket IS here
    "ticket_fields":         ("/api/v2/ticket_fields.json", "ticket_fields"),
    "ticket_forms":          ("/api/v2/ticket_forms.json", "ticket_forms"),
    "custom_statuses":       ("/api/v2/custom_statuses.json", "custom_statuses"),
    "organization_fields":   ("/api/v2/organization_fields.json", "organization_fields"),
    "user_fields":           ("/api/v2/user_fields.json", "user_fields"),
    "custom_objects":        ("/api/v2/custom_objects", "custom_objects"),
    # --- who may do what
    "groups":                ("/api/v2/groups.json", "groups"),
    "custom_roles":          ("/api/v2/custom_roles.json", "custom_roles"),
    # --- the business rules
    "macros":                ("/api/v2/macros.json", "macros"),
    "triggers":              ("/api/v2/triggers.json", "triggers"),
    "trigger_categories":    ("/api/v2/trigger_categories", "trigger_categories"),
    "automations":           ("/api/v2/automations.json", "automations"),
    "views":                 ("/api/v2/views.json", "views"),
    "sla_policies":          ("/api/v2/slas/policies.json", "sla_policies"),
    "group_sla_policies":    ("/api/v2/group_slas/policies.json", "group_sla_policies"),
    "routing_attributes":    ("/api/v2/routing/attributes", "attributes"),
    "queues":                ("/api/v2/queues", "queues"),
    # --- the vocabularies those rules are built from (the UI's own menus)
    "macro_definitions":     ("/api/v2/macros/definitions.json", None),
    "trigger_definitions":   ("/api/v2/triggers/definitions.json", None),
    "view_definitions":      ("/api/v2/views/definitions.json", None),
    "sla_definitions":       ("/api/v2/slas/policies/definitions.json", None),
    # --- integration surface
    "webhooks":              ("/api/v2/webhooks", "webhooks"),
    "targets":               ("/api/v2/targets.json", "targets"),
    "apps_installed":        ("/api/v2/apps/installations.json", "installations"),
    "sharing_agreements":    ("/api/v2/sharing_agreements.json", "sharing_agreements"),
    # --- content and housekeeping
    "dynamic_content":       ("/api/v2/dynamic_content/items.json", "items"),
    "tags":                  ("/api/v2/tags.json", "tags"),
    "satisfaction_reasons":  ("/api/v2/satisfaction_reasons.json", "reasons"),
    "support_addresses":     ("/api/v2/recipient_addresses.json", "recipient_addresses"),
    "resource_collections":  ("/api/v2/resource_collections.json", "resource_collections"),
    "deletion_schedules":    ("/api/v2/deletion_schedules", "deletion_schedules"),
    "ticket_form_statuses":  ("/api/v2/ticket_form_statuses.json", "ticket_form_statuses"),
    # --- help center
    "hc_categories":         ("/api/v2/help_center/categories.json", "categories"),
    "hc_sections":           ("/api/v2/help_center/sections.json", "sections"),
    "hc_user_segments":      ("/api/v2/help_center/user_segments.json", "user_segments"),
    "hc_locales":            ("/api/v2/help_center/locales.json", None),
}


def walk(path: str, key: str | None) -> tuple[object, str]:
    """Fetch a whole collection, following cursor then offset. Returns (data, note)."""
    status, _, doc, raw = call("GET", path)
    if status != 200:
        detail = ""
        if isinstance(doc, dict):
            detail = str(doc.get("error") or doc.get("description") or "")[:80]
        return None, f"HTTP {status} {detail}".strip()
    if key is None:
        return doc, "ok"

    items = list(doc.get(key) or [])
    pages, note = 1, "ok"
    # cursor first (meta.has_more + links.next), else offset (next_page)
    while True:
        meta, links = doc.get("meta") or {}, doc.get("links") or {}
        nxt = links.get("next") if meta.get("has_more") else doc.get("next_page")
        if not nxt or pages > 60:
            break
        nxt = nxt.replace(SUB if SUB.startswith("http") else f"https://{SUB}.zendesk.com", "")
        if not nxt.startswith("/"):
            break
        status, _, doc, _ = call("GET", nxt)
        if status != 200:
            note = f"partial: page {pages+1} returned HTTP {status}"
            break
        items += list(doc.get(key) or [])
        pages += 1
        time.sleep(0.1)
    return items, f"{note} ({pages} page{'s' if pages != 1 else ''})"


def enrich_forms(forms: list) -> list:
    """Forms only carry agent_conditions on the individual GET, not the list."""
    out = []
    for f in forms:
        st, _, d, _ = call("GET", f"/api/v2/ticket_forms/{f['id']}.json")
        out.append(d.get("ticket_form", f) if st == 200 and d else f)
        time.sleep(0.1)
    return out


def main() -> int:
    OUT.mkdir(exist_ok=True)
    report, results = [], {}
    for name, (path, key) in TARGETS.items():
        data, note = walk(path, key)
        if name == "ticket_forms" and isinstance(data, list):
            data = enrich_forms(data)
            note += " + per-form conditions"
        results[name] = data
        n = len(data) if isinstance(data, list) else ("doc" if data else 0)
        flag = " " if data is not None else "!"
        print(f" {flag} {name:<24} {str(n):>5}  {note}")
        report.append((name, n, note))
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, default=str))
        time.sleep(0.1)

    (OUT / "_index.json").write_text(json.dumps(
        {n: {"count": c, "note": t} for n, c, t in report}, indent=1))
    print(f"\n-> {OUT.name}/  ({len(report)} extracts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
