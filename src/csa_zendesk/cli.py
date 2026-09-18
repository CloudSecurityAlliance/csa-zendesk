"""The `csa-zendesk` command line: a door into OAuth, nothing more.

Every `NotAuthorised`/`TokenFileError`/etc. message across `auth/*` already tells
an operator to "run `csa-zendesk auth login`" - this module is what makes that
command exist. Three subcommands, on purpose, matching ADR-009's shape and
nothing past it:

  `auth login`   run the OAuth flow once, persist the result
  `auth whoami`  identity the *stored* credential resolves to, live
  `auth status`  what is on disk right now - no network call

**Output channel, decided deliberately and applied consistently:** this module
lives inside the package, so the same stdout prohibition CLAUDE.md states for
the library applies here too - a stray byte on stdout under stdio MCP corrupts
the JSON-RPC channel, and `tests/test_public_api.py`'s import-time guard walks
every module in the package looking for exactly that. All prompts, narration
and error text go to **stderr**, with one considered exception: `whoami` and
`status` print their *result* - the thing a caller asked for, the kind of
output a script might reasonably pipe or grep - to **stdout**. `login` never
does this, even on success, because its output is a person confirming who they
just authorised, not a value someone captures; the credential itself never
reaches either stream. See Task 9's fix report for the reasoning in full.

Every failure - the five Task 7 distinguishes (not configured, browser never
returned, state mismatch, scope refused, grant refused) and the two this
module adds (no token file, corrupt/wrongly-permissioned token file) - is a
`ZendeskError` subclass with an already-legible message. `main` catches that
one base class at the single dispatch point and prints the message, never a
traceback; an exception outside that hierarchy is a bug and is allowed to
surface as one.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence

from . import auth
from . import exceptions as exc

__all__ = ["main"]


def _human_duration(seconds: float) -> str:
    """`23 minutes`, not `1379.4` - `status`'s whole reason to exist is to be
    read by a person deciding whether to re-authorise."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


def _human_expiry(expires_at: float, *, now: float | None = None) -> str:
    remaining = expires_at - (now if now is not None else time.time())
    if remaining <= 0:
        return f"expired {_human_duration(-remaining)} ago"
    return f"expires in {_human_duration(remaining)}"


def _cmd_login(args: argparse.Namespace) -> int:
    """Run the flow, then confirm identity - "logged in" should mean Zendesk
    recognises the credential, not merely that a token was written to disk."""
    scopes = tuple(os.environ.get("CSA_ZENDESK_SCOPES", "read").split())
    tokens = auth.login(scopes=scopes, open_browser=not args.no_browser, paste=args.paste)
    try:
        identity = auth.whoami()
    except auth.NotAuthenticated as e:
        print(  # noqa: T201 - stderr, not stdout
            f"token written (granted scope: {tokens.scope}), but the identity check "
            f"just after login failed: {e}",
            file=sys.stderr,
        )
        return 1
    name = identity.get("name") or identity.get("email") or "(unnamed account)"
    print(f"Logged in as {name}. Granted scope: {tokens.scope}", file=sys.stderr)  # noqa: T201 - stderr, not stdout
    return 0


def _cmd_whoami(args: argparse.Namespace) -> int:
    """The identity, on stdout - this is the command's one job and answer."""
    identity = auth.whoami()
    for key in ("id", "name", "email", "role"):
        if key in identity:
            # Deliberate stdout, not a stray write: this module is never imported
            # by an embedder's stdio MCP process (it is reached only through the
            # console-script entry point, a separate process invocation), and
            # `whoami`'s whole job is to answer a question a script might pipe.
            print(f"{key}: {identity[key]}")  # noqa: T201 - deliberate stdout, see module docstring
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """What is on disk, without a network call. Never the token itself."""
    path = auth.token_path()
    tokens = auth.read()
    if tokens is None:
        print(f"no token file at {path}", file=sys.stderr)  # noqa: T201 - stderr, not stdout
        print("run `csa-zendesk auth login`", file=sys.stderr)  # noqa: T201 - stderr, not stdout
        return 1
    print(f"token file: {path}")  # noqa: T201 - deliberate stdout, see module docstring
    print(_human_expiry(tokens.expires_at))  # noqa: T201 - deliberate stdout, see module docstring
    print(f"scope: {tokens.scope}")  # noqa: T201 - deliberate stdout, see module docstring
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="csa-zendesk", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="OAuth login and credential status")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", required=True)

    login_parser = auth_sub.add_parser("login", help="run the OAuth login flow once and persist the result")
    login_parser.add_argument(
        "--paste",
        action="store_true",
        help="no local browser reachable: print the URL and read the pasted redirect back",
    )
    login_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="print the authorization URL instead of opening it automatically",
    )
    auth_sub.add_parser("whoami", help="the identity the stored credential resolves to")
    auth_sub.add_parser("status", help="whether a token file exists, its expiry and scope")

    return parser


# `set_defaults(func=...)` would put an `Any`-typed callable on the Namespace and
# lose the return-type check on the way back out of `main` - an explicit dict
# keeps `main` honestly typed for the three commands that exist, nothing more.
_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "login": _cmd_login,
    "whoami": _cmd_whoami,
    "status": _cmd_status,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `csa-zendesk` console script (`pyproject.toml`'s
    `[project.scripts]`). `argv=None` lets argparse read `sys.argv[1:]`
    itself, which is also what the generated console-script wrapper needs -
    it calls `main()` with no arguments."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = _COMMANDS[args.auth_command]
    try:
        return command(args)
    except exc.ZendeskError as e:
        print(str(e), file=sys.stderr)  # noqa: T201 - stderr, not stdout
        return 1
