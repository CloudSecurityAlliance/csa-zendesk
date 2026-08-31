#!/usr/bin/env python3
"""Extract the tool surface of every cloned Zendesk MCP server, from SOURCE.

READMEs lie - they drift from the code, and several of these servers document
tools they do not register. So this parses the actual registration sites across
the SDKs in play (Python low-level and decorator styles, TS/JS object literals,
Go mcp-go).

Clones live in other-zendesk-mcp-servers/ and are gitignored; this script and its
output are about OTHER PEOPLE'S PUBLIC REPOS, so both are safe to commit.

Heuristic by necessity: there is no manifest to read. Counts are indicative
rather than exact, and the interesting output is the SHAPE of the field.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLONES = ROOT / "other-zendesk-mcp-servers"

PATS = [
    re.compile(r'@(?:app|mcp|server)\.tool\(\s*\)?\s*(?:async\s+)?def\s+([a-z][a-z0-9_]*)', re.I),
    re.compile(r'@(?:app|mcp|server)\.tool\([^)]*name\s*=\s*["\']([a-z][a-z0-9_.\-]*)["\']', re.I),
    re.compile(r'types\.Tool\(\s*name\s*=\s*["\']([a-z][a-z0-9_.\-]*)["\']', re.I),
    re.compile(r'\bTool\(\s*name\s*=\s*["\']([a-z][a-z0-9_.\-]*)["\']', re.I),
    re.compile(r'(?:server|mcp)\.(?:tool|registerTool)\(\s*["\']([a-z][a-z0-9_.\-]*)["\']', re.I),
    re.compile(r'\bname:\s*["\']([a-z][a-z0-9_]*_[a-z0-9_]*)["\']'),
    re.compile(r'mcp\.NewTool\(\s*"([a-z][a-z0-9_.\-]*)"', re.I),
]
NOISE = {"name", "type", "string", "object", "number", "boolean", "default",
         "description", "content", "text", "error", "result", "input_schema",
         "inputschema", "properties", "required", "items", "enum", "format"}
CODE_SUFFIX = {".py", ".ts", ".js", ".go", ".mjs", ".tsx", ".cs", ".java"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "vendor", "test", "tests",
             "__tests__", "venv", ".venv", "examples"}

# Domain buckets, matched in order. First hit wins.
DOMAINS = [
    ("help center", r"article|section|categor|translat|label|help_?center|knowledge|post|topic"),
    ("ticket read", r"^(get|list|show|read|count|search)_?ticket|ticket_?(detail|audit|metric|field|form|status)|linked_incident|^get_tickets$"),
    ("ticket write", r"ticket.*(creat|updat|delet|solv|clos|assign|merg|spam|tag|edit)|^(creat|updat|delet|solv|clos|assign|merg)e?_?ticket|bulk_solve|set_ticket|add_tag|remove_tag"),
    ("comments/notes", r"comment|note|repl"),
    ("attachments", r"attach|upload|download"),
    ("users", r"user|requester|agent|identit"),
    ("orgs", r"organi[sz]ation|^org"),
    ("groups", r"group|membership"),
    ("macros", r"macro"),
    ("views/queues", r"view|queue"),
    ("automation rules", r"trigger|automation|sla|webhook|target"),
    ("search", r"search|query"),
    ("time/reporting", r"time|satisfaction|csat|performance|stat|report|analytic"),
    ("auth/meta", r"oauth|auth|token|whoami|health|config|capabilit"),
]


def domain(tool: str) -> str:
    for name, pat in DOMAINS:
        if re.search(pat, tool):
            return name
    return "other"


def normalise(tool: str) -> str:
    t = tool.lower().replace("-", "_")
    for prefix in ("zendesk_", "zd_", "zendesk:"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t


def tools_in(repo_dir: pathlib.Path) -> set[str]:
    found: set[str] = set()
    for f in repo_dir.rglob("*"):
        if not f.is_file() or f.suffix not in CODE_SUFFIX:
            continue
        if set(f.parts) & SKIP_DIRS:
            continue
        try:
            src = f.read_text(errors="ignore")
        except OSError:
            continue
        for pat in PATS:
            for m in pat.finditer(src):
                t = m.group(1)
                if t.lower() in NOISE or len(t) < 4:
                    continue
                found.add(t)
    return found


def main() -> int:
    if not CLONES.is_dir():
        print(f"no clones at {CLONES}", file=sys.stderr); return 1
    servers = {}
    for d in sorted(CLONES.iterdir()):
        if not d.is_dir():
            continue
        repo = d.name.replace("~", "/")
        last = subprocess.run(["git", "-C", str(d), "log", "-1",
                               "--format=%ad", "--date=short"],
                              capture_output=True, text=True).stdout.strip()
        raw = tools_in(d)
        servers[repo] = {"raw": sorted(raw),
                         "norm": sorted({normalise(t) for t in raw}),
                         "last_commit": last,
                         "prefixed": sum(1 for t in raw
                                         if t.lower().startswith(("zendesk_", "zd_")))}

    print(f"{'tools':>5} {'pfx':>4}  {'last':<11} repo")
    for repo, d in sorted(servers.items(), key=lambda kv: -len(kv[1]["norm"])):
        print(f"{len(d['norm']):>5} {d['prefixed']:>4}  {d['last_commit']:<11} {repo}")

    freq = collections.Counter()
    for d in servers.values():
        freq.update(d["norm"])

    print(f"\n{len(freq)} distinct tools; "
          f"{sum(1 for c in freq.values() if c == 1)} in exactly one server\n")

    by_domain = collections.defaultdict(list)
    for tool, count in freq.items():
        by_domain[domain(tool)].append((count, tool))
    print(f"{'domain':<18}{'tools':>6}{'in 3+':>7}  most common")
    for name, _ in DOMAINS + [("other", "")]:
        items = sorted(by_domain.get(name, []), reverse=True)
        if not items:
            continue
        top = ", ".join(f"{t}({c})" for c, t in items[:4])
        print(f"{name:<18}{len(items):>6}{sum(1 for c,_ in items if c>=3):>7}  {top}")

    out = ROOT / "analysis/prior-art-tools.json"
    out.write_text(json.dumps(
        {"servers": servers,
         "frequency": dict(freq.most_common()),
         "by_domain": {k: sorted(v, reverse=True) for k, v in by_domain.items()}},
        indent=1))
    print(f"\n-> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
