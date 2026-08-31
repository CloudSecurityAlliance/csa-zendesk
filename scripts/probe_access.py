#!/usr/bin/env python3
"""Systematic access audit: what can this credential actually reach?

Reads analysis/operation-inventory.csv, picks one representative collection-level
GET per family (a path with no {placeholders}, so it needs no ids), probes it, and
classifies the result. GET ONLY - this script never mutates.

Classification, and the distinctions matter:

  available     200
  needs args    400/422 - the endpoint EXISTS and answers; it wants parameters.
                Counts as available for planning purposes.
  plan-gated    403 - authenticated and refused. A feature or plan boundary,
                not a bug and not a credential problem.
  absent        404 - either not provisioned on this account or a path that no
                longer exists. `InvalidEndpoint` in the body is Zendesk saying
                the route is unknown; a bare 404 is less specific.
  no-collection the family exposes only id-addressed GETs, so it cannot be
                probed blind. NOT a finding - just unmeasured here.

Output goes to tenant-config/ because which families an account can reach is a
fact about that account.
"""
from __future__ import annotations

import collections
import csv
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from zd import call

ROOT = pathlib.Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "analysis/operation-inventory.csv"
OUT = ROOT / "tenant-config/access-audit.json"

# Families whose collection GET needs a parameter we can supply cheaply.
EXTRA_QUERY = {
    "Search": "?query=type:ticket",
    "Incremental Export": "?start_time=1",
    "Incremental Skill Based Routing": "?start_time=1",
    "Help Center Search": "?query=test",
    "Activity Stream": "",
}


def representative(rows: list[dict]) -> str | None:
    """A collection-level GET: no {placeholders}, shortest path wins."""
    cands = [r["path"] for r in rows
             if r["method"] == "GET" and "{" not in r["path"]]
    return min(cands, key=len) if cands else None


def classify(status: int, body) -> str:
    if status == 200:
        return "available"
    if status in (400, 422):
        return "needs args"
    if status == 403:
        return "plan-gated"
    if status == 404:
        return "absent"
    if status in (401,):
        return "auth failed"
    return f"http {status}"


def main() -> int:
    rows = list(csv.DictReader(INVENTORY.open()))
    fams: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        fams[(r["capability"], r["family"])].append(r)

    results = []
    for (cap, fam), frows in sorted(fams.items()):
        path = representative(frows)
        total = len(frows)
        if path is None:
            results.append({"capability": cap, "family": fam, "operations": total,
                            "verdict": "no-collection", "status": None, "path": None})
            continue
        probe = path + EXTRA_QUERY.get(fam, "")
        if not probe.endswith((".json", "/")) and "?" not in probe:
            probe += ".json"
        status, _, body, _ = call("GET", probe)
        verdict = classify(status, body)
        detail = ""
        if isinstance(body, dict) and status >= 400:
            detail = str(body.get("error") or body.get("errors")
                         or body.get("description") or "")[:60]
        results.append({"capability": cap, "family": fam, "operations": total,
                        "verdict": verdict, "status": status, "path": path,
                        "detail": detail})
        time.sleep(0.08)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1))

    by_verdict = collections.Counter(r["verdict"] for r in results)
    ops_by_verdict = collections.Counter()
    for r in results:
        ops_by_verdict[r["verdict"]] += r["operations"]

    print(f"{len(results)} families, {sum(r['operations'] for r in results)} operations\n")
    print(f"{'verdict':<16}{'families':>9}{'operations':>12}")
    for v, n in by_verdict.most_common():
        print(f"{v:<16}{n:>9}{ops_by_verdict[v]:>12}")

    for v in ("plan-gated", "absent", "auth failed"):
        hit = [r for r in results if r["verdict"] == v]
        if not hit:
            continue
        print(f"\n--- {v} ({len(hit)} families, "
              f"{sum(r['operations'] for r in hit)} operations) ---")
        for r in sorted(hit, key=lambda x: -x["operations"]):
            print(f"  {r['operations']:>3} ops  {r['family']:<34} {r['detail']}")

    nc = [r for r in results if r["verdict"] == "no-collection"]
    if nc:
        print(f"\n--- unmeasured: id-addressed only ({len(nc)} families, "
              f"{sum(r['operations'] for r in nc)} operations) ---")
        print("  " + ", ".join(sorted(r["family"] for r in nc)))

    print(f"\n-> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
