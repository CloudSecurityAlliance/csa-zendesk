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
import stat
import tempfile
from dataclasses import dataclass

from .. import exceptions as exc


class TokenFileError(exc.ZendeskError):
    """The token file exists but cannot be trusted."""


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float

    def __repr__(self) -> str:  # never let a credential reach a log line
        return f"Tokens(expires_at={self.expires_at!r}, credentials=<redacted>)"


def token_path() -> pathlib.Path:
    override = os.environ.get("CSA_ZENDESK_TOKEN_FILE")
    if override:
        return pathlib.Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".config"
    return base / "csa-zendesk" / "tokens.json"


def _ensure_dir(path: pathlib.Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)


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
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(
                {
                    "access_token": tokens.access_token,
                    "refresh_token": tokens.refresh_token,
                    "expires_at": tokens.expires_at,
                },
                fh,
            )
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def read() -> Tokens | None:
    """Load the tokens, asserting the file's mode on every read.

    Checked every time rather than at login, because the dangerous case is a file
    that was correct when written and was later chmodded, copied, or restored from
    a backup that did not preserve modes.
    """
    path = token_path()
    if not path.exists():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise TokenFileError(
            f"{path} has mode {mode:04o}, expected 0600. A token file readable by "
            f"anyone else is a finding, not a preference. Fix it with "
            f"`chmod 600 {path}` and consider the credential compromised."
        )
    try:
        raw = json.loads(path.read_text())
        return Tokens(
            access_token=raw["access_token"],
            refresh_token=raw["refresh_token"],
            expires_at=float(raw["expires_at"]),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise TokenFileError(
            f"{path} is not a readable token file ({type(e).__name__}). Delete it and "
            f"run `csa-zendesk auth login` again."
        ) from e


def clear() -> None:
    token_path().unlink(missing_ok=True)
