#!/usr/bin/env python3
"""Assert the design spec's own claims against itself.

The tool count has been stated wrongly twice by hand. A document that asserts a
number about its own contents should have that number checked, not proofread.

Run in CI alongside the other gates.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs/superpowers/specs/2026-09-01-csa-zendesk-design.md"
NON_TOOLS = {"ticket", "people", "hc", "admin", "reporting", "raw", "none",
             "context", "tickets", "help_center", "queues", "export", "status"}


def main() -> int:
    if not SPEC.exists():
        print(f"spec not found: {SPEC}", file=sys.stderr); return 1
    s = SPEC.read_text()
    fails = []

    # 1. the tool count claim vs the table
    table = s[s.index("| Toolset | Tools | Capability |"):s.index("**54 tools**") if "**54 tools**" in s
              else s.index("| Toolset | Tools | Capability |") + 4000]
    tools = {t for t in re.findall(r"`([a-z_]+)`", table)
             if t not in NON_TOOLS and "." not in t}
    m = re.search(r"\*\*(\d+) tools\*\*", s)
    if not m:
        fails.append("no tool-count claim found")
    elif int(m.group(1)) != len(tools):
        fails.append(f"claims {m.group(1)} tools, table lists {len(tools)}")

    # 2. every ADR referenced must exist
    for n in sorted(set(re.findall(r"ADR-(\d{3})", s))):
        if not (ROOT / f"DECISIONS-ADR/ADR-{n}.md").exists():
            fails.append(f"references ADR-{n}, which does not exist")

    # 3. every toolset named in the surface section must appear in the table
    for ts in ("context", "tickets", "help_center", "people", "queues", "reporting",
               "export", "admin", "status"):
        if f"**{ts}**" not in s:
            fails.append(f"toolset `{ts}` is named but has no row in the tool table")

    # 4. no placeholders
    for pat in ("TBD", "TODO:", "FIXME", "XXX"):
        if pat in s:
            fails.append(f"contains placeholder {pat!r}")

    if fails:
        print(f"SPEC CHECK FAILED - {len(fails)} problem(s)")
        for f in fails:
            print(f"  {f}")
        return 1
    print(f"OK - spec claims {len(tools)} tools and lists {len(tools)}; "
          f"all ADR links resolve; all toolsets present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
