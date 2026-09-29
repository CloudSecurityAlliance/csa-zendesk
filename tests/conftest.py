"""Shared test fixtures.

Task 5 wires the ticket-scope allowlist (`_scope.py`) into `PolicyBackend`'s
dispatch (`policy._dispatch` -> `policy.assert_subject_permitted`). Every
pre-existing test that exercises `get_ticket` through a `PolicyBackend`
predates that control and never set an allowlist - and `_scope.read_listing`
fails closed on an unset variable by design ("unset" must never silently mean
"unrestricted"; see `tests/test_scope.py`).

Defaulting both allowlists to `*` here mirrors the normal deployed posture
described in the task 5 brief - "triage must see the whole queue" - and keeps
those pre-existing tests exercising what they were written to exercise, rather
than incidentally also asserting an allowlist default they were never written
to test. A test that DOES care about scope overrides this with its own
`monkeypatch.setenv(...)`, which simply takes precedence within that test.
"""

import pytest


@pytest.fixture(autouse=True)
def _default_allowlists_permit_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_READ", "*")
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "*")


@pytest.fixture(scope="session", autouse=True)
def _pytest_tmp_root_is_private(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Make pytest's temp root actually private, because these tests write credentials.

    `_store._ensure_dir` refuses to put a credential into a directory anyone else can
    reach, and on Windows that refusal is correct about `%TEMP%`: measured on a stock
    box it carries extra principals (a sandbox group, AppContainer SIDs) that `~/.config`
    does not. pytest's `tmp_path` lives under `%TEMP%`, so every test that writes a token
    was asking the store to do the one thing it exists to refuse.

    Hardening the session root once fixes it for every `tmp_path` beneath it: the children
    inherit the hardened ACL, and `is_private` counts inherited ACEs precisely because who
    can read a path does not depend on how the ACE arrived. This is the test environment
    being made to match production - `~/.config` is private on a real machine - rather than
    the product being relaxed to match the test environment.

    A no-op on POSIX beyond `chmod 700`, where `%TEMP%`'s equivalent is `/tmp` and pytest
    already creates per-user roots at `0o700`.
    """
    from csa_zendesk.auth import _privacy

    _privacy.harden(tmp_path_factory.getbasetemp())
