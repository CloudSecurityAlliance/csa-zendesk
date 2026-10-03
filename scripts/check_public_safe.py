#!/usr/bin/env python3
"""Fail if anything tenant-specific is tracked in this repo.

The line this enforces:

    Facts about ZENDESK are public.  Facts about THIS TENANT are not.
    Facts about CSA'S OWN INTERNAL INFRASTRUCTURE are not either.

The third clause was added 2026-10-02 and is not a generalisation of the first two: it
covers a category neither of them reaches. A project-tracker block carrying an Airtable
base id, a table id, a record id and a path into a private repository sat in this public
README until then, and this guard ran clean throughout - correctly, because the class was
not in scope. See csa-google-gmail-calendar#13, which found the same block in three of the
five public repos. What it discloses is internal structure plus a path that 404s for every
external reader; the ids are identifiers, not credentials.

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

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIVATE_TERMS = ROOT / "tenant-config/private-terms.txt"

EXEMPT_SUFFIXES = (".yaml",)          # upstream vendor specs
EXEMPT_PATHS = {"analysis/operation-inventory.csv",   # derived from those specs
                # This file. A denylist of SHAPES cannot be its own corpus: the patterns
                # and the self-test fixtures necessarily look like the thing being hunted,
                # so scanning it reports four findings about itself and none about the
                # repo. The same problem the docstring describes for literals, one level
                # up - and the same answer the email rule already uses, which exempts the
                # one advertised address by exact value rather than exempting SECURITY.md.
                #
                # The exemption is narrowed by SYNTHETIC_FIXTURES below: self_test asserts
                # that EVERY id-shaped string in this source is one of three declared fakes,
                # so pasting a real id in here fails even though the walk skips the file.
                "scripts/check_public_safe.py"}

# The only id-shaped strings allowed to exist in this file. Deliberately sequential so they
# read as placeholders at a glance, and checked rather than trusted - the first draft of
# self_test used the REAL Airtable base id as a fixture, and this guard refused its own
# source file for it. That is the behaviour you want from the guard and the wrong place for
# the value: a test holding the real thing puts it in one more file, the one whose job is to
# print what it finds.
SYNTHETIC_FIXTURES = frozenset({"app0123456789ABCD", "tbl0123456789ABCD",
                                "rec0123456789ABCD"})

# Addresses that are DELIBERATELY published. A SECURITY.md without a contact is
# useless, so the check has to permit the one address it exists to advertise -
# but by exact value, never by exempting the file, so an unrelated address
# appearing in SECURITY.md is still caught.
PUBLISHED_CONTACTS = frozenset({
    "security@cloudsecurityalliance.org",
})

# RFC 2606 / RFC 6761 reserve these for documentation and testing. An address in
# one of them cannot belong to anybody, so it is a fixture by construction - a
# structural exemption, not a list of literals to maintain.
RESERVED_DOMAINS = re.compile(r"@(?:[a-z0-9-]+\.)*(?:example\.(?:com|org|net)|"
                              r"test|invalid|localhost|local)$", re.I)

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
    "CSA-internal Airtable id":
        # An Airtable base/table/record id: the three-letter prefix plus exactly
        # fourteen more. The DIGIT LOOKAHEAD is not decoration - without it this
        # matched `recordCustomEvent`, a 17-character JavaScript identifier, 15
        # times in a sibling repo's minified vendor API docs. An Airtable id always
        # carries at least one digit; that identifier carries none. A guard that
        # cries wolf on vendor documentation earns an exemption, and then the
        # exemption is what is load-bearing.
        re.compile(r"\b(?:app|tbl|rec)(?=[A-Za-z0-9]{14}\b)"
                   r"(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{14}\b"),
    "CINO project-record path":
        # A path into the private CINO project-records repo. Narrow on purpose: banning
        # the ORG NAME outright would fire on this repo's own TODO.md, which cites
        # CINO-Platform-Engineering docs and issues by link. Those 404 for an
        # external reader - a transparency wart - but they disclose nothing beyond
        # the existence of an internal engineering repo. Banning them would force
        # an exemption and leave the pattern meaning nothing.
        re.compile(r"CINO-Projects/projects/"),
}


def self_test() -> None:
    """Break each structural rule on purpose, then assert the exemptions still hold.

    This file had no self-test until 2026-10-02. It is a denylist of regexes over a file
    walk - precisely the construct that passes identically whether or not it still matches
    anything. The two newest patterns are the ones with a live false-positive history, so
    both halves are asserted: that they catch the real shape, and that they do NOT catch
    the things that cost a cycle to discover.
    """
    def hits(label: str, text: str) -> bool:
        return bool(STRUCTURAL[label].search(text))

    # SYNTHETIC ids of the real shape. The first draft of this function used the actual
    # base id as its fixture, and this guard refused its own source file for it - which is
    # the behaviour you want, and the reason the fixtures are now obviously fake. A test
    # that holds the real value puts the secret in one more place, in the file whose job is
    # to print what it finds.
    assert hits("CSA-internal Airtable id", "project_tracker_base: Tracker:app0123456789ABCD")
    assert hits("CSA-internal Airtable id", "Projects:tbl0123456789ABCD")
    assert hits("CSA-internal Airtable id", "csa-zendesk:rec0123456789ABCD")
    # Assembled, so the forbidden literal does not appear in this file either.
    forbidden_path = "CINO-" + "Projects/projects/csa-zendesk"
    assert hits("CINO project-record path", "github:org/" + forbidden_path)

    # And what must NOT fire. `recordCustomEvent` is the measured one: 17 characters,
    # `rec` + 14, no digit, 15 occurrences in a sibling repo's minified vendor docs.
    for benign in ("recordCustomEvent", "application", "reconfiguration",
                   "recontextualization", "tablespoons"):
        assert not hits("CSA-internal Airtable id", benign), f"false positive on {benign!r}"

    # Citing an internal DOC or ISSUE by link is deliberately legal - this repo's own
    # TODO.md does it, and banning the org name would force an exemption that hollows the
    # rule out.
    assert not hits("CINO project-record path",
                    "https://github.com/CloudSecurityAlliance-Internal/"
                    "CINO-Platform-Engineering/issues/49")

    # This file is exempt from the walk (see EXEMPT_PATHS), so the exemption is narrowed
    # here: every id-shaped string in this source must be one of the declared fakes. A real
    # id pasted in fails, without this file needing to know what the real one is.
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    stray = set(STRUCTURAL["CSA-internal Airtable id"].findall(src)) - SYNTHETIC_FIXTURES
    assert not stray, (
        f"non-synthetic id-shaped string(s) in this file: {sorted(stray)}. "
        f"Use a SYNTHETIC_FIXTURES value; never the real id."
    )

    # A sanity check on the pre-existing rules, so this function is not only about the new
    # ones: SECURITY.md's advertised address must still be reachable by the email pattern,
    # because PUBLISHED_CONTACTS exempting it by exact value is what makes that safe.
    assert hits("email address", "security@cloudsecurityalliance.org")
    assert PUBLISHED_CONTACTS, "PUBLISHED_CONTACTS is empty; the email rule would fail SECURITY.md"


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
    # BEFORE the walk. A clean bill of health from a denylist that has stopped
    # matching anything reads exactly like a clean repo.
    self_test()
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
                if label == "email address" and (
                        m.group() in PUBLISHED_CONTACTS
                        or RESERVED_DOMAINS.search(m.group())):
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
                n = sum(1 for lbl, _, _ in items if lbl == label)
                print(f"      {label} x{n}  (first at line {line}: {snip!r})")
        print("\nThese identify a tenant or a third party. Keep them out of the public repo.")
        return 1

    print(f"OK - {len(files)} tracked files, nothing tenant-specific found")
    print(f"coverage: {coverage}")
    # A pass either way: STRUCTURAL ONLY (no private term list present) is a
    # deliberately reduced-coverage PASS, not a failure - CI runs this way, since
    # the gitignored tenant term list is absent there by design (see the `coverage`
    # message above, which says so rather than reporting a clean bill of health it
    # cannot support).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
