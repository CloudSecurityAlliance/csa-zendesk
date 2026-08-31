#!/usr/bin/env python3
"""Fail if anything tenant-specific is tracked in this repo.

The line this enforces:

    Facts about ZENDESK are public.  Facts about THIS TENANT are not.

Findings about the vendor's API - that `users/me` answers 200 unauthenticated,
that search caps at 1000, that the Help Center spec declares no pagination -
describe a product millions of people use, and publishing them helps whoever
hits them next. Findings about one organisation's configuration - its process
fields, its group names, its volumes - are that organisation's business.

TWO TIERS, AND THE SECOND ONE IS NOT IN THIS FILE.

An earlier version hardcoded the tenant's own terms as the patterns to hunt for,
which made this script a compact, searchable index of precisely what it existed
to hide: the denylist became the disclosure. So:

  * STRUCTURAL patterns live here. They describe SHAPES - an email address, a
    real subdomain, "<number> macros" - and name no organisation.
  * LITERAL terms live in tenant-config/private-terms.txt, which is gitignored.
    One term per line, '#' comments ignored. Two categories share that file:
    this tenant's own identifiers, and the names of the third-party projects in
    the prior-art survey - which is anonymised because it studies an ecosystem
    to find what is unsolved, not to rank individuals' side projects in a
    corporate repository.

When the private list is absent the script still runs, but says so rather than
reporting a clean bill of health it cannot support - a check that silently covers
less than you think is worse than no check.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIVATE_TERMS = ROOT / "tenant-config/private-terms.txt"

EXEMPT_SUFFIXES = (".yaml",)          # upstream vendor specs
EXEMPT_PATHS = {"analysis/operation-inventory.csv"}   # derived from those specs

# Addresses that are DELIBERATELY published. A SECURITY.md without a contact is
# useless, so the check has to permit the one address it exists to advertise -
# but by exact value, never by exempting the file, so an unrelated address
# appearing in SECURITY.md is still caught.
PUBLISHED_CONTACTS = frozenset({
    "security@cloudsecurityalliance.org",
})

# Documented Zendesk PRODUCT limits. These are comma-formatted counts, but they
# describe the API's behaviour rather than any tenant's data.
PRODUCT_LIMITS = re.compile(r"\b(?:10,000|1,000|100,000|20,000|2,500)\b")

# Shapes, not names. Nothing here identifies an organisation.
STRUCTURAL: dict[str, re.Pattern] = {
    "email address":
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "real Zendesk subdomain":
        # A tenant subdomain. Zendesk's OWN hosts are the product, not a tenant:
        # developer./support./status./www. are public documentation and status.
        re.compile(r"\b(?!example\b|your-?subdomain\b|acme\b|SUB\b|subdomain\b"
                   r"|developer\b|support\b|status\b|www\b)"
                   r"[a-z][a-z0-9-]{3,}\.zendesk\.com"),
    "tenant object count":
        re.compile(r"\b\d{2,}\s+(?:macros|triggers|views|automations|groups|agents|"
                   r"tags|support addresses|ticket fields|forms|sections|categories)\b"),
    "tenant volume figure":
        # A comma-formatted count OF SOMETHING. Documented PRODUCT limits are
        # excluded below - "10,000 records" is Zendesk's offset ceiling, a fact
        # about the API, not about anyone's data.
        re.compile(r"\b\d{1,3},\d{3}\b\s*(?:tickets|users|organizations|records|"
                   r"articles|comments)\b"),
    "real ticket id":
        re.compile(r"(?:ticket|#)\s*#?\s*\d{5,7}\b", re.I),
    "agent-workspace URL":
        re.compile(r"/agent/tickets/\d+"),
}


def literal_terms() -> tuple[list[str], bool]:
    if not PRIVATE_TERMS.exists():
        return [], False
    terms = []
    for line in PRIVATE_TERMS.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            terms.append(line)
    return terms, True


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [f for f in out.stdout.split("\n") if f]


def main() -> int:
    terms, have_private = literal_terms()
    findings: list[tuple[str, str, int, str]] = []
    files = tracked()

    for path in files:
        if path.endswith(EXEMPT_SUFFIXES) or path in EXEMPT_PATHS:
            continue
        try:
            text = open(path, errors="ignore").read()
        except OSError:
            continue
        for label, pat in STRUCTURAL.items():
            for m in pat.finditer(text):
                if label == "tenant volume figure" and PRODUCT_LIMITS.match(m.group()):
                    continue
                if label == "email address" and m.group() in PUBLISHED_CONTACTS:
                    continue
                findings.append((path, label,
                                 text.count("\n", 0, m.start()) + 1, m.group()[:48]))
        low = text.lower()
        for term in terms:
            idx = low.find(term.lower())
            if idx >= 0:
                findings.append((path, "private term",
                                 text.count("\n", 0, idx) + 1, term[:48]))

    coverage = ("structural + tenant terms" if have_private
                else "STRUCTURAL ONLY - tenant-config/private-terms.txt not present, "
                     "so tenant-specific literals were NOT checked")

    if findings:
        by_file: dict[str, list] = {}
        for path, label, line, snip in findings:
            by_file.setdefault(path, []).append((label, line, snip))
        print(f"REFUSED - {len(findings)} findings in {len(by_file)} of {len(files)} files")
        print(f"coverage: {coverage}\n")
        for path, items in sorted(by_file.items()):
            print(f"  {path}")
            seen = set()
            for label, line, snip in items:
                if label in seen:
                    continue
                seen.add(label)
                n = sum(1 for l, _, _ in items if l == label)
                print(f"      {label} x{n}  (first at line {line}: {snip!r})")
        print("\nThese identify a tenant or a third party. Keep them out of the public repo.")
        return 1

    print(f"OK - {len(files)} tracked files, nothing tenant-specific found")
    print(f"coverage: {coverage}")
    return 0 if have_private else 0


if __name__ == "__main__":
    raise SystemExit(main())
