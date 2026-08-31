#!/usr/bin/env python3
"""Enumerate every operation in the snapshotted Zendesk OpenAPI specs.

Emits analysis/operation-inventory.csv (one row per operation) and prints a
per-capability summary. Re-run after refreshing specs/ to see upstream drift.

Zendesk's published YAML does not load with yaml.safe_load: trigger-condition
EXAMPLES contain a bare `=` scalar (`change: =`), which YAML 1.1 resolves to the
special tag:yaml.org,2002:value that SafeLoader refuses. The one-line constructor
below is the whole fix - the file is not corrupt.
"""
from __future__ import annotations

import csv
import pathlib
import sys
from collections import Counter, defaultdict

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPECS = {
    "ticketing":   ROOT / "specs/zendesk-support-oas.yaml",
    "help_center": ROOT / "specs/zendesk-help-center-oas.yaml",
    "voice":       ROOT / "specs/zendesk-voice-oas.yaml",
}
METHODS = ("get", "post", "put", "patch", "delete")


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("tag:yaml.org,2002:value", lambda l, n: l.construct_scalar(n))


def load(path: pathlib.Path) -> dict:
    with path.open() as fh:
        return yaml.load(fh, Loader=_Loader)


def deref(doc: dict, node):
    """Resolve a local $ref. Zendesk puts 177 shared parameters behind refs, so a
    reader that skips this sees `None` where every pagination parameter should be."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node:
        seen += 1
        if seen > 10:
            raise ValueError("$ref cycle")
        cur = doc
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        node = cur
    return node


def paging_of(doc: dict, op: dict) -> str:
    """Which pagination the spec DECLARES. Not what the endpoint supports - the
    Help Center spec declares none at all on 96 GETs that are all paginated."""
    refs = {p.get("$ref", "").rsplit("/", 1)[-1] for p in op.get("parameters") or []}
    if "CursorPaginationPage" in refs:
        return "cursor"
    if "DualPaginationPage" in refs:
        return "dual"
    if "Page" in refs or "PerPage" in refs:
        return "offset"
    names = {(deref(doc, p) or {}).get("name") for p in op.get("parameters") or []}
    if "page" in names or "per_page" in names:
        return "offset"
    return ""


def main() -> int:
    rows = []
    for capability, path in SPECS.items():
        if not path.exists():
            print(f"missing spec: {path}", file=sys.stderr)
            return 1
        doc = load(path)
        for url, item in (doc.get("paths") or {}).items():
            for method, op in (item or {}).items():
                if method not in METHODS:
                    continue
                rows.append({
                    "capability": capability,
                    "family": (op.get("tags") or ["(untagged)"])[0],
                    "method": method.upper(),
                    "path": url,
                    "operation_id": op.get("operationId", ""),
                    "summary": (op.get("summary") or "").strip(),
                    "paging": paging_of(doc, op),
                    "deprecated": "yes" if op.get("deprecated") else "",
                    "sideload": "yes" if any(
                        (deref(doc, p) or {}).get("name") == "include"
                        for p in op.get("parameters") or []) else "",
                })

    rows.sort(key=lambda r: (r["capability"], r["family"], r["path"], r["method"]))
    out = ROOT / "analysis/operation-inventory.csv"
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} operations -> {out.relative_to(ROOT)}\n")
    by_cap: dict[str, list] = defaultdict(list)
    for r in rows:
        by_cap[r["capability"]].append(r)
    for cap, rs in by_cap.items():
        meth = Counter(r["method"] for r in rs)
        pag = Counter(r["paging"] for r in rs if r["paging"])
        print(f"{cap:12s} {len(rs):4d} ops  {len({r['family'] for r in rs}):3d} families  "
              f"{dict(meth)}")
        print(f"{'':12s}      paging declared: {dict(pag) or 'NONE'}  "
              f"sideload: {sum(1 for r in rs if r['sideload'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
