#!/usr/bin/env python3
"""Assert README's operation counts against the analysis CSVs that produce them.

`scripts/check_spec.py` guards the design spec's claims about itself. This does the
same job for `README.md`, which states counts derived from `analysis/` and had no
guard at all.

It was written because the README said **825 of 882 machine-readable operations**,
and the two numbers came from different sets. 882 is the inventory: ticketing 640
+ help_center 182 + voice 60. Status's three operations have no published spec and
are **not in the inventory**, so counting them in the numerator measured 825 against
a denominator that excluded three of them. The in-scope figure is 822 - exactly the
rows in `operation-classification.csv`, which is ticketing + help_center and nothing
else.

That error is not visible by reading. Both numbers are plausible, both are close to
right, and the only way to see it is to compute both sides. Hence a script rather
than a correction.

Run standalone, from the pre-commit hook, or in CI alongside the other gates.
"""
from __future__ import annotations

import collections
import csv
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
INVENTORY = ROOT / "analysis/operation-inventory.csv"
CLASSIFICATION = ROOT / "analysis/operation-classification.csv"

# Capability keys in the CSVs, and the label each one carries in README's table.
CAPABILITY_LABELS = {
    "ticketing": "Ticketing",
    "help_center": "Help Center",
    "voice": "Voice (Talk)",
}


def _rows(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    for path in (README, INVENTORY, CLASSIFICATION):
        if not path.exists():
            print(f"not found: {path}", file=sys.stderr)
            return 1

    inventory = _rows(INVENTORY)
    classification = _rows(CLASSIFICATION)
    s = README.read_text()
    fails: list[str] = []

    total = len(inventory)
    in_scope = len(classification)
    deferred = total - in_scope
    by_capability = collections.Counter(r["capability"] for r in inventory)

    # 1. The headline claim: "N of M machine-readable operations at 1.0.0".
    #    N must be the classified set and M the inventory, or the sentence is
    #    measuring two different populations against each other.
    m = re.search(r"\*\*(\d+) of (\d+) machine-readable operations at 1\.0\.0\*\*", s)
    if not m:
        fails.append("no '**N of M machine-readable operations at 1.0.0**' claim found")
    else:
        claimed_scope, claimed_total = int(m.group(1)), int(m.group(2))
        if claimed_scope != in_scope:
            fails.append(
                f"claims {claimed_scope} operations in scope at 1.0.0; "
                f"operation-classification.csv has {in_scope} rows"
            )
        if claimed_total != total:
            fails.append(
                f"claims {claimed_total} machine-readable operations; "
                f"operation-inventory.csv has {total} rows"
            )

    # 2. Every per-capability count in the table must match the inventory.
    for key, label in CAPABILITY_LABELS.items():
        row = re.search(rf"^\|\s*{re.escape(label)}\s*\|\s*(\d+)\s*\|", s, re.MULTILINE)
        if not row:
            fails.append(f"no table row found for capability {label!r}")
        elif int(row.group(1)) != by_capability[key]:
            fails.append(
                f"table says {label} = {row.group(1)}; "
                f"inventory has {by_capability[key]}"
            )

    # 3. Any other bare restatement of the inventory total must agree with it.
    #    Catches the file registry line and the scripts/inventory.py comment.
    for stated in {int(n) for n in re.findall(r"(\d{3}) (?:rows|operations)\b", s)}:
        if stated not in (total, in_scope, deferred):
            fails.append(
                f"states '{stated} rows/operations', which is none of "
                f"inventory={total}, in-scope={in_scope}, deferred={deferred}"
            )

    # 4. The deferred figure, wherever it appears, is total minus in-scope.
    for stated in {int(n) for n in re.findall(r"(\d+) (?:operations )?deferred", s)}:
        if stated != deferred:
            fails.append(f"says {stated} deferred; inventory minus in-scope is {deferred}")

    if fails:
        print(f"COUNT CHECK FAILED - {len(fails)} problem(s)")
        for f in fails:
            print(f"  {f}")
        return 1

    print(
        f"OK - README agrees with analysis/: {in_scope} of {total} in scope at 1.0.0, "
        f"{deferred} deferred ("
        + ", ".join(f"{label} {by_capability[key]}" for key, label in CAPABILITY_LABELS.items())
        + ")"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
