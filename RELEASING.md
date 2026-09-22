# Releasing

**Decision, 2026-09-21: yes, this goes on PyPI.** The previous version of this document
deliberately left that open — "a CSA-internal tool with a public repo, not (yet) something
outside consumers are asking to `pip install`" — and said the infrastructure below should
not be built before the question was answered deliberately. It has been: the fleet is
shipping several of these servers, both siblings (`csa-google-workspace`, `csa-skilljar`)
are published, and an operator installing one of them and not this one is the odd case. The
name was also unclaimed on PyPI, which is its own argument for claiming it.

Publishing is **CI-only, off a published GitHub Release, via PyPI Trusted Publishing
(OIDC)**. No long-lived API token exists to leak. Nothing is ever published from a laptop.

The chain each release preserves, per `PUBLIC-GITHUB-REPO-STANDARDS.md` §6:

```
PR → protected main → GitHub Release → CI build → approved, attested OIDC publish
```

Every link is only as strong as branch protection.

## One-time setup

Do these once, in this order. Until all three are done, a release run will fail at the
publish step — which is the correct failure, not a bug.

### 1. PyPI pending publisher

`csa-zendesk` does not exist on PyPI yet, so there is no project to attach a publisher to.
PyPI calls this a **pending publisher**: you configure it first and the project is created
by the first successful upload. At <https://pypi.org/manage/account/publishing/>:

| Field | Value |
|---|---|
| PyPI Project Name | `csa-zendesk` |
| Owner | `CloudSecurityAlliance` |
| Repository name | `csa-zendesk` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

**The environment name is not optional, and this is the one people leave blank.** If it is
blank, PyPI accepts an OIDC token from *any* environment in that repo+workflow. The
workflow still says `environment: pypi`, the approval gate still looks configured — and it
is enforced only by a line of YAML inside the repository being published, which anyone able
to edit the workflow can delete. PyPI notices and emails *"Trusted Publisher … can be made
more secure"* on every publish.

If you ever need to change the binding: **add the constrained publisher first, confirm it,
then remove the unconstrained one** — so there is never a window with no working publisher.

### 2. GitHub Environment `pypi`

Settings → Environments → `pypi` → **Required reviewers**. This makes a release pause for an
explicit approval before anything is uploaded.

Be precise about what this buys, because the honest description is narrower than "required
reviewer" sounds. It gives a **pause**, an **audit record** of who approved and when, and
**decoupling** of publishing from merging. It does *not* give separation of duties if the
approver is the same principal that opened the PR, merged it and cut the release — that is
one principal agreeing with itself. Claim the first three; do not claim the fourth.

### 3. Branch protection on `main`

Required status checks: `lint`, `test (3.10)`, `test (3.11)`, `test (3.12)`, `test (3.13)`,
`gates`, `security`.

`scripts/check_controls.py` asserts all three of the above and reports `OK` / `VIOLATED` /
`UNVERIFIABLE`, never collapsing the third into the first. It runs on the release path
(non-strict, so an outage cannot redden a release) and weekly via `controls.yml`
(`--strict`, because nobody is watching that run). Run it by hand any time:

```bash
python scripts/check_controls.py
```

---

## Cutting a release

1. **Land everything through a PR** and let `main` go green.

2. **Bump the version in one place** — `src/csa_zendesk/__init__.py`'s `__version__`.
   `pyproject.toml` reads it from there by static AST parse, so there is no second place to
   forget. Land the bump and the CHANGELOG entry as an ordinary PR.

3. **Promote `[Unreleased]` in `CHANGELOG.md`** to the version and date.

4. **Check locally before tagging:**
   ```bash
   .venv/bin/python -m pytest -q --cov=csa_zendesk --cov-report=term-missing
   .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check src tests
   .venv/bin/python -m mypy
   .venv/bin/python scripts/check_public_safe.py
   .venv/bin/python scripts/check_boundaries.py
   .venv/bin/python scripts/check_controls.py
   ```

5. **Create the GitHub Release.** This creates the tag *and* fires `release.yml`:
   ```bash
   gh release create v0.2.0 --title v0.2.0 --notes-file <(sed -n '/## \[0.2.0\]/,/^## /p' CHANGELOG.md | head -n -1)
   ```
   **Tag == version.** `v0.2.0` must equal `__version__`. The tag is the provenance anchor:
   `git checkout v0.2.0` must reproduce exactly what shipped.

6. **Approve the pending deployment.** The `publish` job waits on the `pypi` environment:
   ```bash
   RUN=$(gh run list --workflow=release.yml --limit 1 --json databaseId -q '.[0].databaseId')
   ENV_ID=$(gh api repos/CloudSecurityAlliance/csa-zendesk/environments/pypi --jq .id)
   gh api --method POST "repos/CloudSecurityAlliance/csa-zendesk/actions/runs/$RUN/pending_deployments" --input - <<JSON
   {"environment_ids":[$ENV_ID],"state":"approved","comment":"why this is safe to ship"}
   JSON
   ```
   Use `--input -`. `gh api -f 'environment_ids[]=…'` breaks under zsh, which glob-expands
   the brackets.

7. **Verify the publish actually landed, and that it is attested.**
   ```bash
   # PEP 740 provenance lives at the integrity endpoint. The project JSON endpoint has NO
   # `provenance` key whether or not the release is attested, so checking there proves
   # nothing either way.
   WHEEL=$(curl -s https://pypi.org/pypi/csa-zendesk/0.2.0/json | python3 -c \
     "import json,sys; print([u['filename'] for u in json.load(sys.stdin)['urls'] if u['filename'].endswith('.whl')][0])")
   curl -s "https://pypi.org/integrity/csa-zendesk/0.2.0/$WHEEL/provenance" | head -c 400

   # --no-cache-dir always: pip caches the index OUTSIDE any venv, so a fresh venv is not
   # a fresh view. PyPI's CDN edges also lag independently, so retry rather than concluding
   # the publish failed.
   python -m pip download --no-cache-dir --no-deps -d /tmp/verify csa-zendesk==0.2.0
   ```


---

## Invariants

- **The tag must equal the version.** The tag is the provenance anchor — `git checkout
  vX.Y.Z` must reproduce exactly what shipped.
- **A PyPI version is permanent.** It can be yanked, never re-uploaded. Fix forward.
- **The published README is frozen per release.** A documentation-only fix reaches PyPI
  only on the next version bump — if a published page is wrong, that is a patch release.
- **Pre-1.0, only the latest release is supported.** No backports.
- **Never publish from a developer machine.**
- **Do not weaken a gate to get a release out.** The gates run *before* upload precisely
  because upload is the irreversible step.

## Already done, recorded so nobody redoes it

- **`py.typed` (PEP 561) ships and was verified inside the built wheel**, not assumed —
  `python -m build --wheel` was run at 0.1.0 and `unzip -l` confirmed `csa_zendesk/py.typed`
  lands in it. Without this a `mypy --strict` library type-checks as `Any` for consumers.
- **The sdist was inspected** and contains only `LICENSE`, `PKG-INFO`, `pyproject.toml`,
  `README.md`, `setup.cfg`, `src` and `tests` — no `analysis/`, `experiments/`, `specs/` or
  `docs/`. That was incidental to setuptools' defaults with nothing declaring the intent,
  which is why `release.yml` now greps the tarball rather than trusting it.

## Changelog discipline

Say plainly what was **not** verified, not only what was added. An oversold changelog is
worse than a terse one, because the next reader calibrates against it. Follow
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) headings; the changelog is the
source of the GitHub release notes (`PUBLIC-GITHUB-REPO-STANDARDS.md` §1).

## Known gaps

- **No hash-pinned lockfiles.** The sibling `csa-google-workspace` installs from
  `requirements/*.txt` with `--require-hashes` and sets `PIP_CONSTRAINT` so even the build
  backend resolves from a recorded closure. csa-zendesk does not, so the suite and the build
  backend resolve from PyPI at release time. This is a **reproducibility** gap, not a
  credential gap — the build/publish job split is what protects the publishing credential.
  Do not describe this pipeline as reproducible until lockfiles exist. Tracked in `TODO.md`.
