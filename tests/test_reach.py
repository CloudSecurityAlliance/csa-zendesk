import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk import policy


def test_the_capability_alone_is_not_enough(monkeypatch):
    # DEC-015: reach is a first-class control with an operator switch SEPARATE
    # from the capability profile, off by default. Holding ticket.reply and
    # nothing else must still refuse.
    monkeypatch.delenv("CSA_ZD_ALLOW_REACH", raising=False)
    assert policy.reach_permitted() is False


def test_the_switch_must_be_explicit(monkeypatch):
    for value in ("", "0", "no", "false", "maybe"):
        monkeypatch.setenv("CSA_ZD_ALLOW_REACH", value)
        assert policy.reach_permitted() is False, value
    monkeypatch.setenv("CSA_ZD_ALLOW_REACH", "true")
    assert policy.reach_permitted() is True


def test_the_refusal_names_the_switch_not_the_capability(monkeypatch):
    # "Every refusal names its own remedy." Telling an operator to grant
    # ticket.reply when they already hold it sends them to the wrong knob.
    monkeypatch.delenv("CSA_ZD_ALLOW_REACH", raising=False)
    with pytest.raises(exc.PolicyError, match="CSA_ZD_ALLOW_REACH"):
        policy.assert_reach_permitted("reply_publicly")
