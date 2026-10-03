# Friction

What cost time here, so it costs it once. Bugs go to
[issues](https://github.com/CloudSecurityAlliance/csa-zendesk/issues); this is for the friction
that recurs because nothing in the repo remembers it. Each entry names its issue.

## F1 — A console script is `foo.exe`, so `Path.exists()` on the POSIX name is always False (#80)

The install check looked for the entry point by its POSIX name. On Windows the installed file is
`csa-zendesk-mcp.exe`, so the check reported **"the server extra is not installed"** on a machine
where it was — and then printed a remedy sending the user to fix something that was not broken.

`shutil.which` is the fix, with two edges that cost their own cycle:

- it honours `PATHEXT` but returns **`PATHEXT`'s casing** (`.EXE`), so `.resolve()` is needed
  before comparing paths;
- it is **stricter than `exists()` on POSIX** because it requires the execute bit — so a test
  fixture that merely creates the file passes on Windows and fails on macOS. The helper in
  `tests/test_cli.py` creates the platform-correct name *and* sets the exec bit for that reason.

csa-google-workspace#511 is the same defect, found independently. The transferable form: **a
check for "is it installed" must ask the thing that resolves executables, not the filesystem.**

## F2 — Ctrl-C did nothing for up to 300 seconds during `authenticate` (#79)

The OAuth callback waited on a single blocking `handle_request()` with the full read budget, so
an interrupt could not be noticed until it returned. Measured with
`_thread.interrupt_main()`: **8.00s to surface with one 8s call, 1.03s with a 0.5s slice.**

The fix loops `handle_request()` to a deadline with `_POLL_SECONDS = 0.5` while **the handler
keeps the full read budget** — the point being that shortening the *poll* must not shorten the
time a slow browser is allowed to finish its request. Those are two different timeouts that look
like one.

Worth remembering because the first version of the test asserted against `_POLL_SECONDS` and so
failed with `AttributeError` on the old code — **red for the wrong reason**, which proves nothing.
It now asserts a literal 1.5s bound.

## F3 — A denylist of private terms is itself the disclosure

`check_public_safe.py`'s first version hardcoded the tenant's own identifiers as the patterns to
hunt for, which made the script *"a compact, searchable index of precisely what it existed to
hide."*

The resolution is the two-tier split that is still in place: **shapes** in the script, **literals**
in a gitignored `tenant-config/private-terms.txt`. The cost is that the literal tier runs on one
machine only, which [`OPERATIONAL-RESOURCES.md`](OPERATIONAL-RESOURCES.md) records — and the
script reports `STRUCTURAL ONLY` rather than a clean bill of health it cannot support.

**A guard that silently covers less than you think is worse than no guard**, and the honest skip
line is what makes the design work.

## F4 — The guard's own source cannot be scanned by the guard

Adding the CSA-internal-identifier class (#84) produced four findings — all in
`check_public_safe.py` itself, because a denylist of shapes necessarily contains the shapes.
Worse, the first `self_test` used the **real** Airtable base id as a fixture, so the guard
refused its own source for holding the thing it hunts.

Both are now handled: the file is in `EXEMPT_PATHS`, and the exemption is *narrowed* by
`self_test` asserting that every id-shaped string in the source is one of three declared
`SYNTHETIC_FIXTURES` — so pasting a real id in still fails, without the file needing to know what
the real one is.

The general rule: **a test fixture must never be the real value**, and the way to enforce that is
structural, not a reminder.

## F5 — The local release checklist assumed a platform it never named (#74)

`RELEASING.md` used `.venv/bin/python` in every line (Windows is `.venv/Scripts/python.exe`) and
gated coverage at 100 locally, which **cannot pass on either platform alone** —
`auth/_privacy.py` branches on the platform, so 100% is the union. A Windows maintainer following
it got a red line that was correct and meant nothing was wrong.

And a red line that is always expected stops being read.

Fixed by stating the platform once and moving the gate to CI. Note the near-miss: the first fix
used `--no-cov` while the prose claimed you could still see which lines were unexecuted — false,
`--no-cov` turns `--cov-report=term-missing` into a warning. Caught by running the command that
had just been documented.

## F6 — `ruff` is not on `PATH`; it is in the repo's `.venv`

Minor but recurring: `ruff check .` fails with *command not found* while
`.venv/Scripts/ruff.exe check .` passes. Same for `mypy`. The release checklist already uses the
venv-relative form; ad-hoc commands are where this bites.

Related, and sharper: **run the gates this repo has, not the ones the last repo had.** CI here
runs `ruff format --check src tests` — *not* `scripts/` — so formatting `scripts/` would be
reformatting on a rule that does not apply to it.
