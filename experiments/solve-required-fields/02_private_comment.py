#!/usr/bin/env python3
"""Step 2: add an INTERNAL (private) note. public=False is the whole difference."""
import os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"))
from zd import call

if not TID:
    raise SystemExit("set ZD_TEST_TICKET to a disposable ticket id")

TID = int(os.environ.get("ZD_TEST_TICKET", "0"))  # export ZD_TEST_TICKET=<id>
body = {"ticket": {"comment": {
    "body": "Internal note from csa-zendesk API probe (2026-08-31). Testing whether "
            "the API reports required-field constraints the way the agent UI does. "
            "No ticket fields were changed by this call.",
    "public": False}}}
status, hdr, doc, raw = call("PUT", f"/api/v2/tickets/{TID}.json", body)
print(f"HTTP {status}")
t = (doc or {}).get("ticket", {})
print(f"  status after:  {t.get('status')}")
print(f"  updated_at:    {t.get('updated_at')}")
audit = (doc or {}).get("audit", {})
for ev in audit.get("events", []):
    if ev.get("type") == "Comment":
        print(f"  comment id={ev.get('id')}  public={ev.get('public')}  "
              f"author={ev.get('author_id')}")
if status >= 400:
    print("  RAW:", raw[:600])
