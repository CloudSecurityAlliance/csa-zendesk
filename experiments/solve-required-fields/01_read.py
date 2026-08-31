#!/usr/bin/env python3
"""Step 1: read the test ticket and record its state before we touch anything.

Redacts follower/requester names - the ticket has four human followers and this
output is written next to a git repo.
"""
import json, os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"))
from zd import call

if not TID:
    raise SystemExit("set ZD_TEST_TICKET to a disposable ticket id")

TID = int(os.environ.get("ZD_TEST_TICKET", "0"))  # export ZD_TEST_TICKET=<id>
status, hdr, doc, raw = call("GET", f"/api/v2/tickets/{TID}.json")
print(f"HTTP {status}")
t = (doc or {}).get("ticket", {})
for k in ("id", "subject", "status", "priority", "type", "ticket_form_id",
          "group_id", "assignee_id", "requester_id", "brand_id", "tags",
          "created_at", "updated_at"):
    print(f"  {k:<16} {t.get(k)}")
cf = {c["id"]: c["value"] for c in t.get("custom_fields", [])}
setf = {k: v for k, v in cf.items() if v not in (None, "", [])}
print(f"  custom_fields    {len(cf)} present, {len(setf)} set")
for k, v in setf.items():
    print(f"      {k}: {v!r}")
print(f"  followers        {len(t.get('follower_ids') or [])} (names withheld)")
print(f"  collaborators    {len(t.get('collaborator_ids') or [])}")

pathlib.Path(__file__).with_name("before.json").write_text(json.dumps(
    {"status": t.get("status"), "custom_fields": t.get("custom_fields"),
     "assignee_id": t.get("assignee_id"), "group_id": t.get("group_id"),
     "ticket_form_id": t.get("ticket_form_id"), "tags": t.get("tags")}, indent=1))
print("\n  -> before.json written (for restore)")
