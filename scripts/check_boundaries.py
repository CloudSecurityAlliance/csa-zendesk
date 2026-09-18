#!/usr/bin/env python3
"""Fail if the tool-boundary table is not bucket-pure.

ADR-016: a tool is (operation × constrained arguments), and the constraint is
what makes the tool bucket-pure. This is the enforcement of that sentence - the table
is hand-written, and a hand-written table drifts.
"""
from __future__ import annotations

import collections
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
AXES = ("effect", "reversibility", "reach", "capability")


def main() -> int:
    with (ROOT / "analysis/tool-boundaries.csv").open() as fh:
        rows = list(csv.DictReader(fh))

    problems: list[str] = []
    by_op: dict[tuple[str, str], list[dict[str, str]]] = collections.defaultdict(list)
    for r in rows:
        by_op[(r["method"], r["path"])].append(r)
        if not r["capability"] or " " in r["capability"]:
            problems.append(f"{r['tool']}: needs exactly one capability, got {r['capability']!r}")

    for op, tools in by_op.items():
        if len(tools) == 1:
            continue
        for t in tools:
            if t["constraint"] == "none":
                problems.append(f"{t['tool']}: shares {op[0]} {op[1]} with a sibling but constrains nothing")
        keys = [tuple(t[a] for a in AXES) + (t["constraint"],) for t in tools]
        if len(set(keys)) != len(keys):
            problems.append(f"{op[0]} {op[1]}: two tools are indistinguishable")

    for p in problems:
        print(f"  {p}", file=sys.stderr)
    print(f"{'REFUSED' if problems else 'OK'} - {len(rows)} tools over {len(by_op)} operations", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
