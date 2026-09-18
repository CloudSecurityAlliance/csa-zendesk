#!/usr/bin/env python3
"""Join the scope triage onto the operation classification and report it.

The counts in SCOPING-TRIAGE.md are produced by this script, never typed. A
family with no assignment is an error rather than a default, because silently
defaulting is how an operation ends up built with nobody having decided it
should be.
"""

from __future__ import annotations

import collections
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUCKETS = ("now", "later", "never", "blocked")


def load():
    ops = list(csv.DictReader((ROOT / "analysis/operation-classification.csv").open()))
    fam = {r["family"]: r for r in csv.DictReader((ROOT / "analysis/scope-triage.csv").open())}
    exc = {(r["method"], r["path"]): r for r in csv.DictReader((ROOT / "analysis/scope-triage-exceptions.csv").open())}
    return ops, fam, exc


def bucket_of(op, fam, exc):
    """Per-operation exceptions win over the family assignment."""
    hit = exc.get((op["method"], op["path"]))
    return (hit["bucket"], True) if hit else (fam[op["family"]]["bucket"], False)


def main() -> int:
    ops, fam, exc = load()

    unassigned = sorted({o["family"] for o in ops} - set(fam))
    if unassigned:
        print(f"ERROR: {len(unassigned)} families have no bucket: {unassigned}", file=sys.stderr)
        return 1
    bad = {b for r in fam.values() if (b := r["bucket"]) not in BUCKETS}
    if bad:
        print(f"ERROR: unknown bucket(s) {sorted(bad)}", file=sys.stderr)
        return 1
    unused = sorted((m, p) for (m, p) in exc if not any(o["method"] == m and o["path"] == p for o in ops))
    if unused:
        print(f"ERROR: exceptions match no operation: {unused}", file=sys.stderr)
        return 1

    counts = collections.Counter()
    fam_counts = collections.Counter()
    exceptions_applied = []
    for o in ops:
        b, is_exc = bucket_of(o, fam, exc)
        counts[b] += 1
        if is_exc:
            exceptions_applied.append((b, o["method"], o["path"]))
    for r in fam.values():
        fam_counts[r["bucket"]] += 1

    total = sum(counts.values())
    print(f"{'bucket':9} {'families':>9} {'operations':>11}   share")
    for b in BUCKETS:
        print(f"{b:9} {fam_counts[b]:>9} {counts[b]:>11}   {counts[b] / total:6.1%}")
    print(f"{'total':9} {sum(fam_counts.values()):>9} {total:>11}")
    print()
    print(
        f"admitted now: {counts['now']} of {total} operations "
        f"({counts['now'] / total:.1%}) across {fam_counts['now']} families"
    )
    print(
        f"refused outright: {counts['never']} operations "
        f"({fam_counts['never']} whole families + {len(exceptions_applied)} exceptions)"
    )
    print()
    print("per-operation exceptions applied:")
    for b, m, p in sorted(exceptions_applied):
        print(f"  {b:8} {m:6} {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
