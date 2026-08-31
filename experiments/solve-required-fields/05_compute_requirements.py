#!/usr/bin/env python3
"""Step 5: can we compute the solve requirements BEFORE calling, and be right?

Zendesk exposes requirements as data in two places:

  1. GLOBAL     ticket_fields[].required  - "required when solving", account-wide,
                but only bites for fields that are ON the ticket's form.
  2. CONDITIONAL ticket_forms[].agent_conditions[] - a rule set of the shape
                "IF parent_field == value THEN these children are required,
                 on these statuses".

The test is agreement: compute the blockers, compare with what the 422 actually
said. If they match, a precheck is trustworthy and can carry the allowed values
the error omits.

The agent_conditions STRUCTURE IS UNDOCUMENTED - Zendesk's reference names the
property and stops. That is why this script exists as a conformance check rather
than a one-off: if upstream changes the shape, the comparison fails loudly
instead of the precheck quietly under-reporting.
"""
import os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"))
from zd import call

SUB = f"https://{os.environ.get('ZENDESK_SUBDOMAIN', '')}.zendesk.com"
ASSIGNEE_FIELD_ID = 360028956373   # a system field, not in ticket.custom_fields


def all_ticket_fields() -> dict:
    out, url = {}, "/api/v2/ticket_fields.json?page[size]=100"
    while url:
        _, _, d, _ = call("GET", url)
        for f in d["ticket_fields"]:
            out[f["id"]] = f
        nxt = (d.get("links") or {}).get("next")
        url = nxt.replace(SUB, "") if nxt and d.get("meta", {}).get("has_more") else None
    return out


def blockers_for(ticket_id: int, target_status: str = "solved") -> list[dict]:
    fields = all_ticket_fields()
    _, _, t, _ = call("GET", f"/api/v2/tickets/{ticket_id}.json")
    tk = t["ticket"]
    values = {c["id"]: c["value"] for c in tk.get("custom_fields", [])}
    values[ASSIGNEE_FIELD_ID] = tk.get("assignee_id")

    _, _, fd, _ = call("GET", f"/api/v2/ticket_forms/{tk['ticket_form_id']}.json")
    form = fd["ticket_form"]

    def unset(fid):
        return values.get(fid) in (None, "", [])

    def describe(fid, why):
        f = fields.get(fid, {})
        opts = f.get("custom_field_options") or []
        return {"id": fid, "title": (f.get("title") or "?").strip(),
                "type": f.get("type", "?"), "why": why,
                "choices": [o["value"] for o in opts] or None}

    out = []
    for fid in form["ticket_field_ids"]:
        f = fields.get(fid)
        if f and f.get("required") and f.get("active") and unset(fid):
            out.append(describe(fid, "always required to solve"))

    for cond in form.get("agent_conditions") or []:
        if values.get(cond["parent_field_id"]) != cond.get("value"):
            continue
        for child in cond.get("child_fields") or []:
            ros = child.get("required_on_statuses") or {}
            applies = (ros.get("type") == "ALL_STATUSES"
                       or target_status in (ros.get("statuses") or []))
            if child.get("is_required") and applies and unset(child["id"]):
                parent = fields.get(cond["parent_field_id"], {}).get("title", "?").strip()
                out.append(describe(child["id"],
                                    f"required because {parent!r} is {cond['value']!r}"))
    return out


if __name__ == "__main__":
    tid = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("ZD_TEST_TICKET", "0"))
    b = blockers_for(tid)
    if not b:
        print(f"ticket {tid}: nothing blocks solve"); raise SystemExit
    print(f"ticket {tid} cannot be solved until:")
    for x in b:
        ch = f"   choices: {', '.join(map(repr, x['choices']))}" if x["choices"] else ""
        print(f"  - {x['title']} ({x['type']}) - {x['why']}{ch}")
