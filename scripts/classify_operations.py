#!/usr/bin/env python3
"""Classify every in-scope operation by action, blast radius and recoverability.

This is the evidence behind ADR-010's capability model, and it becomes the input
to policy._GATES. Deriving the model from the data rather than from one workflow
is the point: the ticket ladder in ADR-003 is right for tickets and was wrong when
generalised by eye - it gave the sharpest gate to the scariest-feeling action
rather than to the widest-reaching one.

Emits analysis/operation-classification.csv. Offline; reads only the inventory.
"""
from __future__ import annotations

import collections
import csv
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
IN = ROOT / "analysis/operation-inventory.csv"
OUT = ROOT / "analysis/operation-classification.csv"
IN_SCOPE = {"ticketing", "help_center"}

# Config surfaces: one call changes behaviour for every future ticket.
CONFIG = re.compile(r"trigger|automation|macro|/views|sla|ticket_field|ticket_form|custom_field|"
                    r"webhook|target|schedule|routing|brand|custom_role|account/settings|"
                    r"dynamic_content|user_field|organization_field|permission|"
                    r"sharing_agreement|resource_collection", re.I)
BULK = re.compile(r"_many|/bulk|/import|create_many|update_many|destroy_many", re.I)
# The API's own vocabulary for the point of no return.
PERMANENT = re.compile(r"permanently|/deleted_tickets/|/deleted_users/|purge|obfuscate", re.I)
MERGE = re.compile(r"/merge$|/merge\.json$", re.I)
SUSPEND = re.compile(r"mark_as_spam|suspend", re.I)
OUTWARD = re.compile(r"side_conversation|/notify|email_notification|satisfaction_ratings/"
                     r"|/requests|send", re.I)


def action(method: str) -> str:
    if method == "GET":
        return "read"
    if method == "DELETE":
        return "delete"
    return "write"


def radius(path: str) -> str:
    if CONFIG.search(path):
        return "config"
    if BULK.search(path):
        return "bulk"
    return "record"


def recoverability(row: dict) -> str:
    """Least recoverable classification wins."""
    blob = row["path"] + " " + row["summary"]
    if PERMANENT.search(blob):
        return "irreversible-permanent"
    if MERGE.search(row["path"]) or SUSPEND.search(blob):
        return "irreversible"
    if row["method"] == "DELETE":
        return "recoverable-with-effort"   # Zendesk soft-deletes most things first
    if row["method"] == "GET":
        return "n/a"
    return "reversible"


def domain(row: dict) -> str:
    p, fam = row["path"], row["family"]
    if row["capability"] == "help_center":
        return "hc"
    if CONFIG.search(p):
        return "admin"
    if re.search(r"/users|/organizations|identit|group_membership|organization_membership"
                 r"|/groups", p, re.I):
        return "people"
    return "ticket"


def main() -> int:
    rows = [r for r in csv.DictReader(IN.open()) if r["capability"] in IN_SCOPE]
    out = []
    for r in rows:
        out.append({
            "capability_area": r["capability"], "family": r["family"],
            "method": r["method"], "path": r["path"],
            "domain": domain(r), "action": action(r["method"]),
            "radius": radius(r["path"]), "recoverability": recoverability(r),
            "outward_facing": "yes" if OUTWARD.search(r["path"]) else "",
        })
    with OUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)

    print(f"{len(out)} in-scope operations -> {OUT.relative_to(ROOT)}\n")
    print(f"{'domain':<9}{'read':>6}{'write':>7}{'delete':>8}   {'config':>7}{'bulk':>6}")
    by = collections.defaultdict(collections.Counter)
    for o in out:
        by[o["domain"]][o["action"]] += 1
        if o["radius"] != "record":
            by[o["domain"]][o["radius"]] += 1
    for dom in ("ticket", "people", "hc", "admin"):
        c = by[dom]
        print(f"{dom:<9}{c['read']:>6}{c['write']:>7}{c['delete']:>8}   "
              f"{c['config']:>7}{c['bulk']:>6}")

    print(f"\n{'recoverability':<26}{'count':>6}")
    for k, n in collections.Counter(o["recoverability"] for o in out).most_common():
        print(f"  {k:<24}{n:>6}")
    print(f"\noutward-facing operations: {sum(1 for o in out if o['outward_facing'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
