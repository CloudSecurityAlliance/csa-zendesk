"""Platform capabilities the suite probes for, rather than infers from `sys.platform`.

Each of these guards a test whose PRECONDITION cannot be built here - not a test whose
subject is broken. That distinction is the point: a skip says the property is UNVERIFIED
on this machine, never that it does not apply. csa-google-workspace#452 is what the other
reading costs - three mitigations named in a threat model turned out to be POSIX-only
no-ops and nothing said so.

Probed rather than keyed to `sys.platform`, because the answers differ *within* Windows.
Symlink creation needs `SeCreateSymbolicLinkPrivilege`, which an ordinary developer shell
lacks and GitHub's `windows-latest` runner holds - so these tests skip locally and RUN in
CI, which is exactly where the proof is worth having.
"""

import pathlib
import tempfile

import pytest


def _can_create_symlinks() -> bool:
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        try:
            (root / "link").symlink_to(root / "target")
        except (OSError, NotImplementedError, AttributeError):
            return False
        return True


def _chmod_changes_who_can_read() -> bool:
    """Does `chmod` actually move the group/other bits?

    On Windows it honours the read-only bit and nothing else, so `chmod(0o777)` leaves
    `stat()` reporting exactly what it reported before and a test that builds a
    "world-readable file" that way has built nothing.
    """
    import os
    import stat

    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "probe"
        p.write_text("x", encoding="utf-8")
        p.chmod(0o600)
        before = stat.S_IMODE(os.stat(p).st_mode)
        p.chmod(0o666)
        after = stat.S_IMODE(os.stat(p).st_mode)
        return before != after


CAN_CREATE_SYMLINKS = _can_create_symlinks()
CHMOD_CHANGES_ACCESS = _chmod_changes_who_can_read()

requires_symlinks = pytest.mark.skipif(
    not CAN_CREATE_SYMLINKS,
    reason=(
        "this process cannot create symlinks, so the behaviour this proves is UNVERIFIED "
        "here (Windows needs an elevated shell or Developer Mode; see #71)"
    ),
)

requires_posix_modes = pytest.mark.skipif(
    not CHMOD_CHANGES_ACCESS,
    reason=(
        "chmod does not change who can read a file here, so a 'world-readable' or "
        "'looser than 0700' precondition cannot be built with it (Windows; see #71). "
        "The equivalent property is asserted through `_privacy.is_private`, which uses "
        "the ACL on this platform."
    ),
)
