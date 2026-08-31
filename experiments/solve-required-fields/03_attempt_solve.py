#!/usr/bin/env python3
"""Step 3: the experiment.

The agent UI refuses to submit this ticket as solved and says, in red:

    "Estimated minutes spent resolving " needed
    "a required choice field" needed

Does the API enforce the same constraint, and if so does it say WHICH fields?
The whole shape of update_ticket depends on the answer.
"""
import json, os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"))
from zd import call

if not TID:
    raise SystemExit("set ZD_TEST_TICKET to a disposable ticket id")

TID = int(os.environ.get("ZD_TEST_TICKET", "0"))  # export ZD_TEST_TICKET=<id>
status, hdr, doc, raw = call("PUT", f"/api/v2/tickets/{TID}.json",
                             {"ticket": {"status": "solved"}})
print(f"HTTP {status}\n")
print("--- RAW RESPONSE BODY (verbatim) ---")
print(raw[:2000])
print("--- END ---\n")
if doc and "ticket" in doc:
    print(f"  resulting status: {doc['ticket'].get('status')}")
pathlib.Path(__file__).with_name("solve_attempt_response.json").write_text(
    json.dumps({"http_status": status, "body": doc if doc else raw}, indent=1))
