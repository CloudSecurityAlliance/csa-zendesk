# Releasing

Two parts: what this repo actually does today (a GitHub release, nothing more), and what
PyPI publishing will require whenever that decision is made. See `TODO.md` for the open
question of whether this package belongs on PyPI at all — it has not been decided, and
this document is not the place that decides it.

## What we do today

There is no PyPI package and no release-automation workflow yet. A release is: bump,
changelog, branch, PR, merge, tag, GitHub release. Nothing is uploaded anywhere.

1. **Bump the version** in `src/csa_zendesk/__init__.py` (`__version__`) — the single
   source of truth; `pyproject.toml` reads it dynamically via
   `version = { attr = "csa_zendesk.__version__" }`. There is exactly one place to change.

2. **Add a dated `CHANGELOG.md` entry** under a new `## [X.Y.Z] — YYYY-MM-DD` heading,
   above the entries it supersedes. Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
   section headings (`Added` / `Changed` / `Fixed` / `Removed` / etc.). Say plainly what
   was **not** verified, not only what was added — an oversold changelog is worse than a
   terse one, because the next reader calibrates against it.

3. **Branch and PR** — never commit the bump directly to `main`:
   ```bash
   git checkout -b release/X.Y.Z
   git add src/csa_zendesk/__init__.py CHANGELOG.md
   git commit -m "chore: release X.Y.Z"
   git push -u origin release/X.Y.Z
   gh pr create --title "release: X.Y.Z" --body "..."
   ```

4. **Get CI green, then merge.** `main` is protected: PRs required, admins enforced, no
   force-push. Required checks: lint (`ruff check` / `ruff format --check`), `mypy --strict`,
   the 3.10–3.13 test matrix, the 100% coverage floor, and `scripts/check_public_safe.py`
   (see `.github/workflows/tests.yml`).

5. **Tag the merge commit and cut a GitHub release:**
   ```bash
   git checkout main && git pull
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <(sed -n '/## \[X.Y.Z\]/,/## \[/p' CHANGELOG.md | sed '$d')
   ```
   Or paste the changelog entry into `gh release create --notes` by hand — the changelog
   is the source of release notes (`PUBLIC-GITHUB-REPO-STANDARDS.md` §1).

6. **Verify the tag matches what merged.** `git checkout vX.Y.Z` should reproduce the
   commit whose CI you just watched pass — there is no build step between merge and tag
   today, so this is mostly a sanity check that the tag landed on the right commit.

That is the entire procedure. No package is built, nothing is uploaded, and no credential
is used beyond ordinary `git`/`gh` push access.

## What PyPI publishing will require (plan, not yet implemented)

**Nothing below exists in this repo yet.** No `release.yml` workflow, no PyPI project, no
Trusted Publisher, no protected `pypi` environment. This section exists so whoever picks
this up knows the shape of the work and the one-time steps that need a human, per
`PUBLIC-GITHUB-REPO-STANDARDS.md` §6 — read that section in full before building any of
this; it is the source of every requirement named here, including the reasoning for each.

**Before any of this is built**, decide the question this document deliberately leaves
open: should `csa-zendesk` be on PyPI at all? It is a CSA-internal tool with a public repo,
not (yet) something outside consumers are asking to `pip install`. See the TODO entry this
release adds — the infrastructure below is real work, and building it before that decision
is made is effort spent on the wrong branch of the question.

If the answer is yes, the release path becomes:

```
PR → protected main → tag → CI build → attested OIDC publish
```

**In the CI workflow:**
- Publishing runs **only from CI, triggered by a pushed tag** — never `twine upload` from a
  developer machine. The point of the whole chain is that the artifact's provenance
  (which commit, built by what, published how) is answerable without trusting anyone's
  laptop.
- **Trusted Publishing / OIDC**, not a stored API token. The publish job requests a
  short-lived token per run via `id-token: write`; no long-lived PyPI token ever exists in
  a GitHub secret to leak or rotate.
- **Every action pinned to a full commit SHA**, with the version in a trailing comment
  (matching the style already in `.github/workflows/tests.yml`) — a mutable tag like `@v4`
  on the job holding `id-token: write` is exactly the seam an attacker uses to exfiltrate
  the publish token and ship as this project.
- **Least-privilege `permissions`** on every job — default read-only, `id-token: write`
  granted only to the one job that publishes.
- The release job runs the **full test suite plus a security scan** (`pip-audit` and a
  static analyzer) again, not just what ran on the PR — a CVE disclosed after the last
  merge should not ship silently.
- **Build attestations (PEP 740)** are emitted, and the workflow (or a manual post-publish
  check) verifies they actually landed on the index — "on by default" is not verification.
- **A dist-contents guard**: fail the build if anything credential-shaped, or `analysis/`,
  `tenant-config/`, or research/experiment material, reached the sdist or wheel.
- **`tag == packaged version`**, checked explicitly and failing the release if they
  disagree.

**One-time setup a human does, once, before the first PyPI release:**
1. ~~Ship a `py.typed` marker (PEP 561) and confirm the built wheel actually contains it~~
   — **done as of 0.1.0**: `src/csa_zendesk/py.typed` exists, `pyproject.toml`'s
   `[tool.setuptools.package-data]` ships it, and `python -m build --wheel` was run and
   the resulting wheel's contents inspected (`unzip -l`) to confirm `csa_zendesk/py.typed`
   actually lands inside it — not assumed. Without this, a fully `mypy --strict` library
   would type-check as `Any` for every consumer.
2. **On PyPI:** register the project name, then add a **Trusted Publisher** naming this
   exact repo (`CloudSecurityAlliance/csa-zendesk`), the workflow filename (e.g.
   `release.yml`), and — critically — the **environment** (`pypi`). A blank environment
   field means the index accepts a token from *any* environment in this repo, which
   defeats the environment-gate below; PyPI emails a "can be made more secure" warning on
   every publish until this is fixed. Add the constrained publisher, confirm it works, then
   (if one was ever added unconstrained) remove the unconstrained entry — in that order, so
   publishing never breaks in between.
3. **On GitHub:** create a `pypi` environment (Settings → Environments) with a **required
   reviewer**. This makes a release **pause** for an explicit approval before anything
   uploads, and gives an **audit record** of who approved and when — real properties, worth
   having. It is **not** separation of duties unless the reviewer is a genuinely different
   authenticated principal from whoever opened the PR and cut the tag; do not describe it
   as two-person control unless that is actually true. Scope any branch/tag protection
   rule on this environment to the release tags, not `main` — a tag-triggered release
   deploys against the tag ref, and a `main`-scoped rule would block the publish outright.
4. **Verify the controls, not just their presence.** `PUBLIC-GITHUB-REPO-STANDARDS.md` §10
   describes a `scripts/check_controls.py` pattern (run, not forked, from
   `csa-google-workspace`) that checks the Trusted Publisher's environment binding, the
   reviewer requirement, and branch protection independently of what the workflow file
   claims — because none of those three live in the repo tree, so no diff and no green CI
   run shows whether they are still on.

**Explicitly out of scope for now:** none of the above is built. This section is a plan to
execute later, not a checklist partially done. Revisit it only after the PyPI-or-not
decision above is made deliberately, not by default.

## Invariants (once PyPI publishing exists)

- **The tag must equal the version.** The tag is the provenance anchor — `git checkout
  vX.Y.Z` must reproduce exactly what shipped.
- **A PyPI version is permanent.** It can be yanked, never re-uploaded. Fix forward.
- **The published README is frozen per release.** A documentation-only fix reaches PyPI
  only on the next version bump.
- **Pre-1.0, only the latest release is supported.** No backports.
