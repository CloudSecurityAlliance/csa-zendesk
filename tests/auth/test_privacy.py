"""`_privacy` - the platform-appropriate answer to "can anyone else reach this".

The Windows half is asserted in the suite that can reach it; what is here runs everywhere
and pins the parts whose behaviour is not platform-specific: the three-state contract
(`True`/`False`/`None`, where `None` is never "secure"), the remedy text, and the pure
string predicate that decides whether a principal is the owner's own logon session.
"""

import os

import pytest

from csa_zendesk.auth import _privacy


def test_an_absent_path_is_unknown_not_private(tmp_path):
    """`None` is not `False`, and it is emphatically not `True`.

    A caller that treats "I could not tell" as "it is fine" has claimed a protection
    nobody verified - the failure csa-google-workspace#452 is a record of.
    """
    assert _privacy.is_private(tmp_path / "nope") is None
    assert _privacy.is_explicitly_hardened(tmp_path / "nope") is None


def test_a_hardened_file_is_private(tmp_path):
    f = tmp_path / "token.json"
    f.write_text("{}", encoding="utf-8")
    _privacy.harden(f)
    assert _privacy.is_private(f) is True


def test_a_hardened_directory_keeps_the_bit_that_makes_it_usable(tmp_path):
    """0o600 on a directory is not "tighter", it is unusable - nothing can traverse in,
    including the next call that needs to write the token."""
    d = tmp_path / "d"
    d.mkdir()
    _privacy.harden(d)
    assert _privacy.is_private(d) is True
    assert os.access(d, os.X_OK), "the directory must still be traversable by its owner"


def test_hardening_through_a_descriptor_agrees_with_hardening_through_a_path(tmp_path):
    f = tmp_path / "by-fd.json"
    fd = os.open(str(f), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        _privacy.harden(f, fd)
    finally:
        os.close(fd)
    assert _privacy.is_private(f) is True


def test_explicitly_hardened_is_at_least_as_strict_as_private(tmp_path):
    """On POSIX the two coincide, because modes do not inherit; on Windows the second is
    strictly stronger. Either way one may never be True while the other is False."""
    f = tmp_path / "token.json"
    f.write_text("{}", encoding="utf-8")
    _privacy.harden(f)
    assert _privacy.is_private(f) is True
    assert _privacy.is_explicitly_hardened(f) in (True, None)


def test_the_remedy_names_the_path_and_is_a_command_for_this_platform(tmp_path):
    """An instruction the reader cannot carry out is worse than none - it costs them the
    time to try it before disbelieving the message. `chmod` is that on Windows."""
    f = tmp_path / "token.json"
    f.write_text("{}", encoding="utf-8")
    text = _privacy.remedy(f)
    assert str(f) in text
    assert ("icacls" in text) == (os.name == "nt")
    assert ("chmod" in text) != (os.name == "nt")


def test_a_directory_remedy_keeps_the_traversal_bit(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    text = _privacy.remedy(d)
    if os.name != "nt":
        assert "700" in text, "600 on a directory would make it untraversable"


@pytest.mark.parametrize(
    ("principal", "expected"),
    [
        (r"NT AUTHORITY\LogonSessionId_0_123456", True),
        (r"nt authority\logonsessionid_0_123456", True),
        (r"NT AUTHORITY\SYSTEM", False),
        (r"CONTOSO\someoneelse", False),
        ("", False),
    ],
)
def test_a_logon_session_sid_is_recognised_case_insensitively(principal, expected):
    """Pure string work, so it is testable on every platform even though only Windows
    ever produces the input. Tolerated rather than removed because a logon session SID is
    strictly NARROWER than "the owner" - and because icacls cannot remove it anyway,
    failing with 1332 ERROR_NONE_MAPPED.
    """
    assert _privacy._is_own_logon_session(principal) is expected


def test_describe_names_the_finding_in_this_platform_s_vocabulary(tmp_path):
    """The old messages echoed the octal mode, which is the useful half of the diagnostic
    on POSIX and meaningless on Windows, where every file reads 0o666 whatever its ACL
    says. Each platform names its own finding rather than one message fitting neither.
    """
    f = tmp_path / "token.json"
    f.write_text("{}", encoding="utf-8")
    _privacy.harden(f)
    text = _privacy.describe(f)
    if os.name == "nt":
        assert "principal" in text or "ACL" in text
    else:
        assert "mode 0600" in text
