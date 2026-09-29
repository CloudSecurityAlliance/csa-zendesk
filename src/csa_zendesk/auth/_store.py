"""The token file. ADR-009: exactly one persisted artifact, holding a credential
and nothing else.

ADR-005 forbids persisting *response* data. A refresh token is a credential, which
is the category SECURITY.md already protects, not customer data, which is the
category ADR-005 exists to keep off disk. Conflating the two produced a
contradiction across three documents; separating them resolves it.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from dataclasses import dataclass

from .. import exceptions as exc
from . import _privacy


class TokenFileError(exc.ZendeskError):
    """The token file exists but cannot be trusted."""


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float
    #: Space-separated scope names exactly as Zendesk granted them at issuance
    #: (login or refresh). NOT a credential - a list of public scope names - so
    #: it is fine to persist and fine to show in `repr`. It exists so refresh
    #: can detect a registered-scope ceiling narrowing since issuance: without a
    #: stored baseline of what this credential actually carried, that check is
    #: only checkable against what the caller happens to be requesting *this
    #: time*, which is empty by default - i.e. no check at all. See Task 5's
    #: fix report for the incident this closes.
    scope: str

    def __repr__(self) -> str:  # never let a credential reach a log line
        return f"Tokens(expires_at={self.expires_at!r}, scope={self.scope!r}, credentials=<redacted>)"


def token_path() -> pathlib.Path:
    override = os.environ.get("CSA_ZENDESK_TOKEN_FILE")
    if override:
        return pathlib.Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".config"
    return base / "csa-zendesk" / "tokens.json"


def _ensure_dir(path: pathlib.Path) -> None:
    """Make sure `path`'s parent directory exists and is private - but only
    `chmod` a directory this call itself creates.

    `path.parent` is `CSA_ZENDESK_TOKEN_FILE`'s directory when that variable is
    set, which can be anywhere: `/var/lib/myservice/zd.json` or
    `$HOME/zd.json` are both legal values. The previous implementation ran
    `path.parent.chmod(0o700)` unconditionally on every write - i.e. every
    refresh - which would silently reduce `/var/lib/myservice` or `$HOME`
    itself to owner-only on the next token rotation, breaking every other
    consumer of that directory. A directory this process did not create is
    verified, not "fixed": a mode that grants group or other access is a loud
    refusal, the same standard `read()` already applies to the file itself.

    This also closes the narrower version of the same bug: `mkdir(mode=0o700,
    parents=True)` applies `mode` to the leaf directory only, so any
    *intermediate* parents it creates along the way are left at the process
    umask. Walking up to the first existing ancestor and `chmod`-ing every
    level created from there down means every directory this call creates -
    leaf or intermediate - ends up private, and nothing this call did not
    create is ever touched.
    """
    directory = path.parent
    if directory.exists():
        private = _privacy.is_private(directory)
        if private is False:
            raise TokenFileError(
                f"{directory} is reachable by someone other than its owner "
                f"({_privacy.describe(directory)}). Refusing to "
                f"write a credential into it - this directory was not created by this "
                f"tool, so it is verified rather than silently corrected out from under "
                f"whoever does own it. {_privacy.remedy(directory)}, or point "
                f"CSA_ZENDESK_TOKEN_FILE somewhere this process can own outright."
            )
        if private is None:  # pragma: no cover - only when icacls cannot run
            # UNKNOWN IS NOT SECURE, and it is also not a finding. Refusing here would
            # mean a machine whose ACL tool is unavailable can never log in, having been
            # told nothing is wrong with its directory - so the honest response is to say
            # so and continue, the same asymmetry `_privacy.harden` applies when icacls
            # fails. The alternative, treating unknown as private, is the one direction
            # this codebase does not take.
            print(  # noqa: T201 - stderr, never stdout: stdout is the MCP JSON-RPC channel
                f"Warning: could not determine whether {directory} is private; "
                f"continuing. The credential's protection is UNVERIFIED.",
                file=sys.stderr,
            )
        return
    to_create: list[pathlib.Path] = []
    current = directory
    while not current.exists():
        to_create.append(current)
        current = current.parent
    for created in reversed(to_create):
        created.mkdir(mode=0o700, exist_ok=True)
        # Belt-and-braces on POSIX, where mkdir's mode argument is still umask-masked; the
        # ONLY thing that restricts the directory on Windows, where the mode argument is
        # ignored outright and an inherited ACL is what a new directory otherwise gets.
        _privacy.harden(created)


def write(tokens: Tokens) -> None:
    """Atomically replace the token file.

    Temp file then rename, because refresh rotates the token and two MCP clients
    may run this server concurrently (ADR-009). A partial write would otherwise
    leave a file that parses as valid JSON and authenticates as nothing.

    The mode is set on the temp file *before* the rename, so the credential is
    never briefly world-readable under its final name.
    """
    path = token_path()
    _ensure_dir(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tokens-", suffix=".json")
    # Tracks whether WE still owe a close. `os.fdopen` takes ownership, so this is set
    # to None the moment it succeeds and the cleanup below knows not to double-close.
    unclosed: int | None = fd
    try:
        # Before a byte is written, on both platforms: POSIX fchmods the descriptor,
        # Windows rewrites the ACL of the (still empty) file. `os.fchmod` DOES NOT EXIST
        # on Windows, which is what this line used to call unconditionally - it raised
        # AttributeError before the write, and the cleanup below then failed to unlink a
        # file whose descriptor was still open, reporting WinError 32 and hiding the
        # actual cause (#71).
        _privacy.harden(tmp, fd)
        with os.fdopen(fd, "w") as fh:
            unclosed = None  # fdopen owns it now; the with-block closes it
            json.dump(
                {
                    "access_token": tokens.access_token,
                    "refresh_token": tokens.refresh_token,
                    "expires_at": tokens.expires_at,
                    "scope": tokens.scope,
                },
                fh,
            )
        # An explicit, non-inherited ACL travels with the file across a same-volume
        # rename, so the Windows hardening survives this exactly as the POSIX mode does.
        os.replace(tmp, path)
    except BaseException:
        # Close before unlinking. On POSIX an open descriptor does not prevent unlink, so
        # this was invisible there; on Windows it is the difference between cleaning up
        # and raising a second, misleading error on top of the first.
        if unclosed is not None:
            os.close(unclosed)
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def read() -> Tokens | None:
    """Load the tokens, asserting the file's mode on every read.

    Checked every time rather than at login, because the dangerous case is a file
    that was correct when written and was later chmodded, copied, or restored from
    a backup that did not preserve modes.

    `path.exists()` is `False` for a symlink whose target is gone - the same
    return value as "no token file at all" - which would make `auth status`
    report a perfectly ordinary-looking "no token file, run `auth login`" for
    a dangling symlink instead of the thing actually wrong (fix or remove the
    symlink). Checked for and named explicitly, before that ambiguity can
    happen.

    `OSError` is caught alongside the pre-existing `(ValueError, KeyError,
    TypeError)` so a `PermissionError` - on the mode check or the read itself -
    is reported the same way every other unreadable-token-file case is,
    instead of escaping as a raw traceback from `auth status`, the one command
    whose entire job is to report what state you are in without ever raising
    one.
    """
    path = token_path()
    if not path.exists():
        if path.is_symlink():
            raise TokenFileError(
                f"{path} is a symlink whose target does not exist. This is not the same "
                f"as no token file - fix or remove the symlink, then run `csa-zendesk auth "
                f"login` if a fresh token is actually needed."
            )
        return None
    try:
        # `is False`, not `not ...`: None means the ACL could not be read, which is
        # unknown rather than compromised, and refusing to read a credential on unknown
        # would strand a user whose icacls is unavailable. Only a definite answer is a
        # finding. See `_privacy` on why unknown is never reported as private.
        if _privacy.is_private(path) is False:
            raise TokenFileError(
                f"{path} is readable by someone other than its owner "
                f"({_privacy.describe(path)}). A token file "
                f"readable by anyone else is a finding, not a preference. "
                f"{_privacy.remedy(path)} and consider the credential compromised."
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        # `raw["scope"]` (not `raw.get("scope", "")`): a file written before scope
        # was tracked is refused via the same KeyError path as any other missing
        # field, rather than silently treated as "no scope" - an empty grant is
        # exactly the value that would make refresh's scope-narrowing check
        # vacuous again, which is the defect this field exists to close.
        return Tokens(
            access_token=raw["access_token"],
            refresh_token=raw["refresh_token"],
            expires_at=float(raw["expires_at"]),
            scope=raw["scope"],
        )
    except (ValueError, KeyError, TypeError, OSError) as e:
        raise TokenFileError(
            f"{path} is not a readable token file ({type(e).__name__}). Delete it and "
            f"run `csa-zendesk auth login` again."
        ) from e


def clear() -> None:
    token_path().unlink(missing_ok=True)
