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
