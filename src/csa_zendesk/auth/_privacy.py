r"""Is this path private, and how do we make it so - by whatever mechanism the platform has.

**Ported from `csa-google-workspace`'s `auth.py`** (its `_harden`, `file_is_owner_only`,
`_read_acl`, `_strays`, `_unexpected_principals`, `_current_windows_principal`). That
implementation was measured on real Windows machines and carries edge cases nobody would
predict from the documentation; re-deriving it here would have meant rediscovering them.
The duplication is deliberate and known: these two servers share no library, and two
copies of security code drift. If a third server needs it, extract a package rather than
taking a third copy.

Why this module exists at all
-----------------------------
`_store.py` asked `mode & 0o077` of a directory and `mode != 0o600` of the token file.
Both are the POSIX *answer* to "can anyone else read this", and on Windows they are not
merely unavailable - they are actively wrong. Windows does not map ACLs onto mode bits, so
`os.stat` reports `0o777` for every directory and `0o666` for every file. The directory
check therefore refused **every** write on **every** Windows machine: 41 of this repo's 51
first-ever Windows test failures were that one line, and a stock install could not store a
credential at all (#71).

So the question is asked as a question. On POSIX it is still the mode bits. On Windows it
is the ACL, read through `icacls`.

Two questions, not one
----------------------
The port needed a split the original does not have, and getting this wrong would have
reproduced the same refusal for a new reason:

- `is_private` - *can anyone other than the owner reach this right now?* This is what
  gates a write. It counts INHERITED ACEs, because who can read a file today does not
  depend on how the ACE granting it arrived.
- `is_explicitly_hardened` - *did `harden` set this ACL, rather than a parent?* Stricter,
  and used to assert `harden` did its job. It rejects inherited ACEs, because an inherited
  ACL changes the moment the directory is re-permissioned.

`None` is not `False`
---------------------
A path that is absent, or an `icacls` that could not run, is *unknown*. Reporting unknown
as private is the dangerous direction, so it never happens here. What a caller does with
unknown is decided at the call site - see `_store._ensure_dir`.
"""

from __future__ import annotations

import os
import stat
import subprocess  # nosec B404 - icacls only, fixed argv, no shell; see `harden`
import sys

_WINDOWS = os.name == "nt"

# The principals a private path may name on Windows: the current user, plus the two
# root-equivalents. Excluding SYSTEM or Administrators would stop nothing, because an
# administrator can take ownership of any file - exactly as `root` reads a 0o600 file on
# POSIX. Tolerating them is the faithful analogue of 0o600, not a concession.
_WINDOWS_ROOT_EQUIVALENTS = ("NT AUTHORITY\\SYSTEM", "BUILTIN\\Administrators")

# Principals that ARE the owner, spelled differently. Not root-equivalents but OWNER-
# equivalents, and tolerated for a stronger reason than SYSTEM and Administrators are:
# these grant rights to whoever owns the object, so they can never name a third party.
#
#   OWNER RIGHTS   (S-1-3-4) - the current owner, explicitly. Measured on a stock
#                  Windows 11 box, every `tempfile.mkdtemp` directory carries it.
#   CREATOR OWNER  (S-1-3-0) - resolves, per object, to whoever created it. In an
#                  inheritable ACE this is how a directory says 'your files are yours'.
#
# The csa-google-workspace original does not list these and would not have needed to:
# it only ever asks about files it hardened itself, and `/inheritance:r` has already
# removed them by then. Asking the question of a directory SOMEBODY ELSE made is what
# surfaces them - and treating 'the owner' as a stray refuses every temp directory on
# the box, which is precisely the 41-test failure this change exists to remove.
_WINDOWS_OWNER_EQUIVALENTS = ("OWNER RIGHTS", "CREATOR OWNER")


def _current_windows_principal() -> str:  # pragma: no cover - Windows-only
    return f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")


def _icacls(*args: str) -> subprocess.CompletedProcess[str]:  # pragma: no cover - Windows-only
    # Fixed argv, no shell, and every path is one this process constructed.
    return subprocess.run(
        ["icacls", *args],
        capture_output=True,
        text=True,  # nosec B603 B607
        check=False,
    )


def _read_acl(path: str) -> tuple[list[str], list[str]] | None:  # pragma: no cover - Windows-only
    r"""(explicit principals, inherited principals), or None if the ACL could not be read.

    One parser, because every predicate below asks the same question of the same output
    and a second copy is how they would drift apart.

    BOTH lists, and this is where this module departs from the csa-google-workspace
    original. That one returns `(explicit, any_inherited_flag)` and discards the inherited
    principals, which is right for the question it asks - *did we harden this file* - and
    wrong for the question `_store._ensure_dir` asks, which is *may a credential go into
    this directory somebody else made*. Measured on a stock Windows 11 profile:

        C:\Users\<user>\.config  NT AUTHORITY\SYSTEM:(I)(OI)(CI)(F)
                                 BUILTIN\Administrators:(I)(OI)(CI)(F)
                                 <HOST>\<user>:(I)(OI)(CI)(F)

    Every ACE inherited, none explicit - so a predicate keyed on the inheritance flag
    answers False for a directory that is in fact reachable only by its owner and the two
    root-equivalents. Porting that predicate unchanged would have kept the very refusal
    this change exists to remove, on every stock machine, for a new reason.
    """
    result = _icacls(path)
    if result.returncode != 0:
        return None
    explicit: list[str] = []
    inherited: list[str] = []
    for raw in result.stdout.splitlines():
        line = raw.removeprefix(path).strip()
        if not line or line.startswith("Successfully processed"):
            continue
        # `DOMAIN\user:(I)(OI)(CI)(F)` - rsplit, because a principal contains no colon but
        # a path prefix would. Everything after the last colon is the rights mask.
        principal = line.rsplit(":", 1)[0].strip()
        (inherited if "(I)" in line else explicit).append(principal)
    return explicit, inherited


def _is_own_logon_session(principal: str) -> bool:
    r"""Is this the LOGON SESSION SID - the owner's own session, not a third party?

    Windows puts `S-1-5-5-<x>-<y>` in the default DACL of files created by some processes,
    and icacls displays it as `NT AUTHORITY\LogonSessionId_0_<id>`. Whether it appears
    depends on the creating process's token: a file created from PowerShell carries it and
    the same code from Git Bash does not.

    Tolerated rather than removed, because of what it identifies. A logon session SID is
    held by exactly the processes in ONE interactive logon of ONE user - strictly NARROWER
    than "the owner", so it grants nothing the owner does not already have, and the next
    logon gets a different SID so a stale ACE grants nothing at all. It also could not be
    removed: `icacls /remove:g` on that display name fails with 1332 ERROR_NONE_MAPPED,
    which is itself the evidence that it is not an ordinary principal.
    """
    return principal.upper().startswith("NT AUTHORITY\\LOGONSESSIONID_")


def strays(principals: list[str]) -> list[str]:  # pragma: no cover - Windows-only
    """Principals that are neither the owner, a root-equivalent, nor its own logon session."""
    allowed = {
        _current_windows_principal().lower(),
        *(p.lower() for p in _WINDOWS_ROOT_EQUIVALENTS),
        *(p.lower() for p in _WINDOWS_OWNER_EQUIVALENTS),
    }
    return [p for p in principals if p.lower() not in allowed and not _is_own_logon_session(p)]


def unexpected_principals(path: str) -> list[str]:  # pragma: no cover - Windows-only
    """Every principal on the ACL, inherited or not, that is not the owner or equivalent.

    `harden` uses this to clean up after `/grant:r`; diagnostics use it to say WHO.
    """
    acl = _read_acl(path)
    if acl is None:
        return []
    return strays([*acl[0], *acl[1]])


def _windows_is_private(path: str) -> bool | None:  # pragma: no cover - Windows-only
    """The Windows half of `is_private`, extracted so it can be excluded as a unit."""
    acl = _read_acl(path)
    if acl is None:
        return None
    principals = [*acl[0], *acl[1]]
    if not principals:
        return None  # icacls said nothing readable; unknown, not secure
    return not strays(principals)


def is_private(path: str | os.PathLike[str]) -> bool | None:
    """Can only the owner reach `path` RIGHT NOW? `None` when that cannot be told.

    Works for a file or a directory. On POSIX that is `mode & 0o077 == 0`; on Windows it
    is every principal on the ACL, inherited ACEs included, checked against the owner and
    the root-equivalents.

    Inheritance is deliberately not part of this answer: who can read the path today does
    not depend on whether the ACE granting it was inherited. Inheritance bears on how
    DURABLE the protection is, which is `is_explicitly_hardened`'s question.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        return None
    if not _WINDOWS:
        return stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0
    return _windows_is_private(path)  # pragma: no cover - reached only on Windows


def is_explicitly_hardened(path: str | os.PathLike[str]) -> bool | None:
    """Did `harden` set this ACL, rather than a parent? Stricter than `is_private`.

    Used to assert that `harden` did its job, never to gate a write. A path that merely
    sits in a well-permissioned directory is `is_private` but not explicitly hardened - it
    inherits, so it changes the moment that directory is re-permissioned or the path
    moves. On POSIX the two coincide, because modes do not inherit.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        return None
    if not _WINDOWS:
        return is_private(path)
    return _windows_explicitly_hardened(path)  # pragma: no cover - reached only on Windows


def _windows_explicitly_hardened(path: str) -> bool | None:  # pragma: no cover - Windows-only
    acl = _read_acl(path)
    if acl is None:
        return None
    explicit, inherited = acl
    if inherited:
        return False  # the ACL is the parent's, not ours
    if not explicit:
        return None  # icacls said nothing readable; unknown, not secure
    return not strays(explicit)


def describe(path: str | os.PathLike[str]) -> str:
    """Say WHAT is wrong, in the vocabulary of the platform that found it.

    The old messages echoed the octal mode - "has mode 0644, expected 0600" - which is the
    useful half of the diagnostic on POSIX and meaningless on Windows, where every file
    reads 0o666 whatever its ACL says. Dropping it to make one message fit both platforms
    would have cost POSIX a real detail; so each platform names its own finding instead,
    and Windows gains one it never had: WHICH principal should not be there.
    """
    path = os.fspath(path)
    if sys.platform == "win32":  # pragma: no cover - Windows-only
        acl = _read_acl(path)
        if acl is None:
            return "the ACL could not be read"
        # An empty stray list means "nothing unexpected", which is NOT the same as "could
        # not tell" - collapsing the two would report a readable-but-clean ACL as unknown.
        extra = strays([*acl[0], *acl[1]])
        return "also reachable by " + ", ".join(extra) if extra else "no unexpected principals"
    return f"mode {stat.S_IMODE(os.stat(path).st_mode):04o}"


def remedy(path: str | os.PathLike[str]) -> str:
    r"""The fix-it-yourself instruction, in the form this platform can actually carry out.

    The old messages said "Run `chmod 700 <dir>`" unconditionally. On Windows that is
    advice the reader cannot act on - `chmod` there sets the read-only bit and nothing
    else, so following it to the letter changes nothing and the error returns unchanged
    next run. An instruction that cannot work is worse than none, because it costs the
    reader the time to try it before disbelieving the message.
    """
    path = os.fspath(path)
    if _WINDOWS:  # pragma: no cover - the branch not taken is excluded on each platform
        return f'Fix it with `icacls "{path}" /inheritance:r /grant:r "%USERDOMAIN%\\%USERNAME%:F"`'
    return f"Fix it with `chmod {'700' if os.path.isdir(path) else '600'} {path}`"


def harden(path: str | os.PathLike[str], fd: int | None = None) -> None:
    """Restrict `path` to its owner, by whatever mechanism the platform actually has.

    On Windows `chmod`/`fchmod` are no-ops for the group/other bits - and `os.fchmod` does
    not exist at all, which is how `write()` raised `AttributeError` there before it had
    written a byte. `icacls /inheritance:r /grant:r <user>:F` is the real equivalent: it
    drops the inherited ACL and leaves exactly one ACE.

    A directory gets the execute bit on POSIX. `0o600` on a directory is not "tighter", it
    is unusable - nothing can traverse into it, including us on the next call. The Windows
    branch has no such distinction, which is exactly why it is easy to lose here.
    """
    path = os.fspath(path)
    if sys.platform == "win32":  # pragma: no cover - Windows-only
        result = _icacls(path, "/inheritance:r", "/grant:r", f"{_current_windows_principal()}:F")
        # AND THEN REMOVE WHATEVER ELSE IS THERE. `/inheritance:r` drops only INHERITED
        # ACEs and `/grant:r` replaces only the ACE for the principal named, so anything
        # explicit that Windows itself put on the path survives both. The process default
        # DACL is the source, and it varies with which shell started the process - a
        # security mechanism whose outcome depends on the parent's token is not one.
        if result.returncode == 0:
            for principal in unexpected_principals(path):
                _icacls(path, "/remove:g", principal)
        else:
            # Warn rather than refuse: failing the write would leave the user unable to
            # log in at all because an ACL tool was unavailable, and they would still have
            # no token. stderr is safe under stdio - only stdout carries JSON-RPC.
            print(  # noqa: T201 - stderr, never stdout: stdout is the MCP JSON-RPC channel
                f"Warning: could not restrict {path} to your account; it inherits the "
                f"directory's permissions. icacls said: "
                f"{result.stderr.strip() or 'nothing'}",
                file=sys.stderr,
            )
        return
    if fd is not None:
        # Reachable only when the branch above did not fire, and mypy knows that
        # because the test is on `sys.platform` - which it narrows - rather than on a
        # module-level bool, which it does not. A `type: ignore` here would be needed
        # on Windows (no `os.fchmod` in those stubs) and reported UNUSED on ubuntu, so
        # the diagnostic would depend on where mypy ran. Narrowing avoids both.
        os.fchmod(fd, 0o700 if os.path.isdir(path) else 0o600)
    else:
        os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)
