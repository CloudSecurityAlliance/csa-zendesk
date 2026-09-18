import pytest

from csa_zendesk import _scope


def test_star_means_everything_and_is_distinct_from_empty(monkeypatch):
    monkeypatch.setenv("X", "*")
    assert _scope.read_listing("X").all_subjects is True


def test_an_unset_variable_permits_nothing(monkeypatch):
    # Fail closed. Absent is not "no restriction".
    monkeypatch.delenv("X", raising=False)
    listing = _scope.read_listing("X")
    assert listing.all_subjects is False and listing.ids == frozenset()
    assert _scope.permits(listing, "44821") is False


def test_entries_parse_with_reasons_and_comments(monkeypatch):
    monkeypatch.setenv("X", "44821  # the triage test ticket\n44822, 44823\n\n# a whole-line comment\n")
    listing = _scope.read_listing("X")
    assert listing.ids == frozenset({"44821", "44822", "44823"})


def test_an_unusable_value_is_an_error_not_a_silent_pass(monkeypatch):
    # ADR-equivalent of csa-google-workspace's third outcome: unusable always
    # means NOTHING permitted, never "ignore the setting".
    monkeypatch.setenv("X", "44821, not-an-id")
    with pytest.raises(_scope.AllowlistError, match="not-an-id"):
        _scope.read_listing("X")


def test_permits_is_exact_not_prefix(monkeypatch):
    monkeypatch.setenv("X", "4482")
    listing = _scope.read_listing("X")
    assert _scope.permits(listing, "4482") is True
    assert _scope.permits(listing, "44821") is False
