#!/usr/bin/env python3
"""Assert the controls that are configured OUTSIDE this repository (T19/T27, #189).

Three things this project relies on live in GitHub's and PyPI's settings, not in the tree:

1. the **PyPI Trusted Publisher binding constrained to the `pypi` environment** — without the
   constraint, the approval gate is enforced only by a line of YAML inside the repo being
   published, so anyone able to edit the workflow could delete it and still publish
   (`RELEASING.md`, *Why the environment name is not optional*);
2. the **`pypi` GitHub Environment still having required reviewers** — it lost them once
   already, noted in v0.21.0;
3. **branch protection on `main` requiring status checks** — the stated premise of
   `dependabot-auto-merge.yml`, the only workflow holding `contents: write` on a PR trigger.

Each was recorded as prose. `RELEASING.md` reasons about the first correctly and at length,
and prose is exactly what does not notice when a setting is toggled and forgotten.

## Three states, and the third is why this script exists

A control check that cannot reach its evidence and reports success is worse than no check: it
reads as a control from the outside while asserting nothing. So every control resolves to
**OK**, **VIOLATED**, or **UNVERIFIABLE**, and the three are never collapsed:

* any **VIOLATED** exits non-zero — the setting is wrong, now;
* **UNVERIFIABLE** is printed as loudly as a failure but does not fail on its own, because the
  commonest cause is a token without admin rights, which is a fact about the caller;
* **everything unverifiable** exits non-zero. Nothing was checked, and a run that checks
  nothing must not look like a clean bill of health.

`--strict` promotes UNVERIFIABLE to a failure, for a caller that knows it holds the rights.

## What this can and cannot tell you

It detects **drift**: a setting changed by hand, a reviewer list emptied, a protection rule
dropped during unrelated repo surgery. That is the realistic failure and it is silent today.

It does **not** defend against someone who can edit this repository, because they can edit
this script. That limit is the same self-referential problem `RELEASING.md` analyses for the
publisher binding, and the same answer applies: the control that survives an attacker with
repo access is the one enforced by PyPI and GitHub, not the one asserting it.

## Credentials

Two of the three need none. `GET /repos/{owner}/{repo}/environments` answers unauthenticated
for a public repo, and PyPI serves the publisher's environment claim in the public PEP 740
provenance. **Branch protection needs admin rights**: it 401s unauthenticated, and a workflow's
`GITHUB_TOKEN` cannot read it either — there is no `administration` permission to grant it. So
in CI that one reports UNVERIFIABLE unless an optional read-only token is supplied.

    python scripts/check_controls.py [--strict] [--repo owner/name]

`GITHUB_TOKEN` or `GH_TOKEN` is used if set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

# This repository's values, and the defaults for the flags below. They are DEFAULTS rather than
# constants because the three controls are not specific to this project: any repo publishing to
# PyPI over Trusted Publishing rests on the same premises, and a sibling repo should be able to
# run this file unmodified rather than fork it. A forked copy is how a check quietly stops
# matching the thing it checks - which is the exact failure this script exists to catch.
REPO = "CloudSecurityAlliance/csa-zendesk"
PACKAGE = "csa-zendesk"
ENVIRONMENT = "pypi"  # `csa-ai-foundation-model-api-clients` calls its environment `release`
BRANCH = "main"
TIMEOUT = 20

OK, VIOLATED, UNVERIFIABLE = "OK", "VIOLATED", "UNVERIFIABLE"


@dataclass
class Result:
    control: str
    state: str
    detail: str

    def line(self) -> str:
        mark = {OK: "ok  ", VIOLATED: "FAIL", UNVERIFIABLE: "????"}[self.state]
        return f"[{mark}] {self.control}\n         {self.detail}"


def get(url: str, token: str | None = None) -> tuple[int, object]:
    """Return (status, parsed-json). Never raises for an HTTP error - the status IS the
    finding, and 401/403 has to be told apart from 200 with the wrong contents.

    The `Accept` header is per-host, not global. Sending GitHub's `application/vnd.github+json`
    to PyPI's integrity endpoint gets a **406**, which is indistinguishable from "unreachable"
    unless you look - so the publisher check silently degraded to UNVERIFIABLE and the run
    still exited 0. Caught by running it, not by reading it.
    """
    github = url.startswith("https://api.github.com/")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json" if github else "application/json, */*",
            "User-Agent": f"{PACKAGE}-control-check",
            **({"Authorization": f"Bearer {token}"} if token and github else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return 0, str(error)


# --- 1. the publisher binding ------------------------------------------------------------
def check_publisher_environment(repo: str, *, package: str = PACKAGE, environment: str = ENVIRONMENT) -> Result:
    """Constrained to `pypi`, read from the published attestation.

    PyPI does not expose a project's publisher CONFIGURATION publicly, so this asserts the
    consequence instead: every PEP 740 provenance carries the publisher's claims, including
    the environment the publishing workflow ran in. If the binding were widened and something
    published from outside the gated environment, it would say so here.

    Being precise about what that buys, since it is evidence rather than configuration: it
    proves what DID publish, not what WOULD be accepted. A binding widened but never abused
    looks identical to a constrained one. That is a real limit - and the widened binding is
    also exactly what PyPI emails about ("Trusted Publisher ... can be made more secure"), so
    the two together cover it from both ends.
    """
    name = f"PyPI Trusted Publisher is constrained to the `{environment}` environment"
    status, data = get(f"https://pypi.org/pypi/{package}/json")
    if status != 200 or not isinstance(data, dict):
        return Result(name, UNVERIFIABLE, f"PyPI unreachable (HTTP {status}).")
    version = data["info"]["version"]
    files = [u["filename"] for u in data["urls"] if u["filename"].endswith(".whl")]
    if not files:
        return Result(name, UNVERIFIABLE, f"{version} has no wheel to check.")

    status, prov = get(f"https://pypi.org/integrity/{package}/{version}/{files[0]}/provenance")
    if status == 404:
        return Result(
            name,
            VIOLATED,
            f"{version} has NO attestation. Either it was not published by the "
            f"trusted publisher, or attestations were turned off.",
        )
    if status != 200 or not isinstance(prov, dict):
        return Result(name, UNVERIFIABLE, f"provenance unreachable (HTTP {status}).")

    bundles = prov.get("attestation_bundles") or []
    if not bundles:
        return Result(name, VIOLATED, f"{version} carries no attestation bundle.")
    publishers = [b.get("publisher", {}) for b in bundles]
    wrong = [p for p in publishers if p.get("environment") != environment or p.get("repository") != repo]
    if wrong:
        return Result(
            name, VIOLATED, f"{version} was published from {wrong!r}, not environment {environment!r} of {repo}."
        )
    return Result(
        name,
        OK,
        f"{version} attests publisher environment={environment!r} "
        f"repository={repo!r} workflow="
        f"{publishers[0].get('workflow')!r}.",
    )


# --- 2. the environment's reviewers ------------------------------------------------------
def check_environment_reviewers(repo: str, token: str | None = None, *, environment: str = ENVIRONMENT) -> Result:
    """The gate that makes a publish stop for a human. It was removed once before."""
    name = f"`{environment}` environment still requires a reviewer"
    status, data = get(f"https://api.github.com/repos/{repo}/environments", token)
    if status != 200 or not isinstance(data, dict):
        return Result(name, UNVERIFIABLE, f"could not read environments (HTTP {status}).")

    matching = [e for e in data.get("environments", []) if e.get("name") == environment]
    if not matching:
        return Result(
            name,
            VIOLATED,
            f"there is no `{environment}` environment. release.yml names it, so "
            f"the publish job is running with no gate at all.",
        )
    rules = [r.get("type") for r in matching[0].get("protection_rules", [])]
    if "required_reviewers" not in rules:
        return Result(
            name,
            VIOLATED,
            f"`{environment}` has protection rules {rules or '[]'} - no required "
            f"reviewers, so a publish proceeds unattended.",
        )
    return Result(name, OK, f"protection rules: {rules}.")


# --- 3. branch protection ----------------------------------------------------------------
def check_branch_protection(repo: str, token: str | None = None, *, branch: str = BRANCH) -> Result:
    """Required status checks on `main`.

    `dependabot-auto-merge.yml` holds `contents: write` on a pull_request trigger and merges
    on green. What stops that from merging something red is branch protection, not the
    workflow - so this is the premise the auto-merge rests on.
    """
    name = f"branch protection on `{branch}` requires status checks"
    status, data = get(f"https://api.github.com/repos/{repo}/branches/{branch}/protection", token)
    if status in (401, 403):
        return Result(
            name,
            UNVERIFIABLE,
            f"needs admin rights (HTTP {status}). A workflow's GITHUB_TOKEN cannot "
            f"read this - there is no `administration` permission to grant it. "
            f"Supply a read-only token with administration:read, or run locally.",
        )
    if status == 404:
        return Result(
            name,
            VIOLATED,
            f"`{branch}` has NO branch protection - anyone with write access can "
            f"push straight to it, and any auto-merge workflow holding "
            f"`contents: write` is merging against nothing. (Here that is "
            f"dependabot-auto-merge.yml; in another repo, check what can merge.)",
        )
    if status != 200 or not isinstance(data, dict):
        return Result(name, UNVERIFIABLE, f"could not read protection (HTTP {status}).")

    checks = (data.get("required_status_checks") or {}).get("contexts") or []
    problems = []
    if not checks:
        problems.append("no required status checks")
    if not (data.get("enforce_admins") or {}).get("enabled"):
        problems.append("not enforced for admins")
    if (data.get("allow_force_pushes") or {}).get("enabled"):
        problems.append("force pushes allowed")
    if (data.get("allow_deletions") or {}).get("enabled"):
        problems.append("branch deletion allowed")
    if problems:
        return Result(name, VIOLATED, f"{'; '.join(problems)}. Required checks: {checks}.")
    return Result(name, OK, f"{len(checks)} required checks, enforced for admins, no force pushes or deletions.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=REPO, help=f"owner/name (default {REPO})")
    parser.add_argument("--package", default=PACKAGE, help=f"PyPI distribution name (default {PACKAGE})")
    parser.add_argument(
        "--environment", default=ENVIRONMENT, help=f"the protected GitHub environment (default {ENVIRONMENT})"
    )
    parser.add_argument("--branch", default=BRANCH, help=f"protected branch (default {BRANCH})")
    parser.add_argument("--strict", action="store_true", help="treat UNVERIFIABLE as a failure")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    results = [
        check_publisher_environment(args.repo, package=args.package, environment=args.environment),
        check_environment_reviewers(args.repo, token, environment=args.environment),
        check_branch_protection(args.repo, token, branch=args.branch),
    ]
    for result in results:
        print(result.line())

    violated = [r for r in results if r.state == VIOLATED]
    unverified = [r for r in results if r.state == UNVERIFIABLE]
    print()

    if violated:
        print(
            f"{len(violated)} control(s) VIOLATED. These are configured outside this "
            f"repository; fix them in GitHub/PyPI settings, not here."
        )
        return 1
    if len(unverified) == len(results):
        print(
            "Nothing could be checked. A run that verifies nothing must not read as a "
            "clean bill of health, so this is a failure rather than a pass."
        )
        return 1
    if unverified:
        print(
            f"{len(unverified)} control(s) could not be checked - see above. Not a "
            f"failure: the usual cause is a token without the rights, which is a fact "
            f"about the caller rather than about the control."
        )
        return 1 if args.strict else 0
    print("All externally-enforced controls verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
