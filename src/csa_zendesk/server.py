"""The MCP stdio server - the first place this library becomes installable.

**Stdout belongs to JSON-RPC.** Under stdio transport, stdout IS the protocol
channel: one stray byte corrupts the session and the server looks alive while
answering nothing. This module never prints; `cli.py` is the package's only
sanctioned exception (a separate process invocation, never imported by an
embedder's stdio MCP process). Diagnostics, if `main` ever needs to emit one
directly, go to stderr - `tests/test_public_api.py`'s import-time guard and
`ruff`'s `T20` selection both police this, and `test_server.py` adds a
registration-time check neither of those two can perform.

**Three tools, read-only, honestly annotated.** `get_ticket`, `search_tickets`
and `list_comments` are the whole surface: `readOnlyHint=True`,
`destructiveHint=False` on every one, because that is what they actually are.
No write tool is registered - `E1_CAPABILITIES` (below), the exact set this
module connects with, would refuse one anyway (it grants only
`TICKET_READ`), but a tool a model can see and cannot use is a worse
experience than one that is simply absent (task brief). This is rung E1 of
the whole-project design's enablement track - "triage the live queue;
propose everything, change nothing" - and it is asserted, not assumed:
`test_the_server_requests_only_read_capabilities` and
`test_no_registered_tool_maps_to_a_write_operation` (Task 7) check both
halves of that claim.

**`READ_TOOLS` and `TOOLS` are named separately on purpose.** `TOOLS` is set to
`READ_TOOLS` here, as a plain list - not re-derived - so that a later task
extending the surface with auth-lifecycle tools (`authenticate`, not
read-only; `logout`, destructive) can write `TOOLS = READ_TOOLS + AUTH_TOOLS`
as an ordinary concatenation. Tests that must keep holding once that happens
(every read tool is read-only and non-destructive) assert against
`READ_TOOLS`, never `TOOLS`, for the same reason.

**Every response passes through `_untrusted` before it leaves this module.**
No tool returns a raw envelope to a model - this is the block's security
property (`_untrusted.py`'s module docstring: ticket text reaches a model as
data, never as instructions). `call_tool_sync` is the one seam every tool
response crosses, which is what makes "no path bypasses the wrap" a claim a
later task can actually test.

**`list_comments` surfaces its own truncation.** `ListTicketComments` caps at
100 comments per page and sorts ascending by creation date
(API-SURFACE.md §5.4g / `client.ZendeskClient.list_comments`'s docstring): a
ticket with more than 100 comments returns the OLDEST 100 and silently drops
the newest - the direction that matters least gets kept and the direction
that carries the triage signal gets dropped, with nothing in the envelope's
shape (`{"comments": [...]}`, no count field) announcing the gap. The backend
returns that raw envelope correctly; making the gap visible is this layer's
job (task brief). `call_tool_sync` checks the comment count it got back
against `_COMMENTS_PAGE_CAP` and, when the count reaches the cap, prepends a
plain-text warning ahead of the (still fully wrapped) envelope - not folded
into the envelope and wrapped as untrusted content itself, because the
warning is this library's own trusted statement about the response's
shape, not requester-authored text, and burying it inside the JSON blob a
model must parse risks exactly the silent miss this exists to prevent. An
exact count of 100 is warned about even though it might be the ticket's
entire history - the endpoint cannot distinguish "exactly 100 comments"
from "100 of many more" any better than this module can, and warning on a
false positive costs a sentence, while missing a real truncation costs a
triage decision made on an incomplete conversation.

**`AUTH_TOOLS` - `authenticate`, `auth_status`, `logout` - make authentication
reachable without leaving the session (TODO E21).** A user of this server who
is logged out, or whose 90-day refresh token has lapsed, would otherwise have
to leave Claude Code, find the right directory and venv, and run `csa-zendesk
auth login` by hand while every tool call fails in the meantime. `TOOLS` is
reassigned here to `READ_TOOLS + AUTH_TOOLS` - a plain concatenation, per the
note above - and `INSTRUCTIONS` is threaded into `build_server()` so a model
that hits `NotAuthorised` knows to call `authenticate` itself rather than
retrying a call that cannot succeed or going looking for a token file on disk
(the precedent is `csa-google-workspace`'s server instructions).

**`logout` exists because [ADR-017](../../DECISIONS-ADR/ADR-017.md) says a
surface that can authenticate must be able to log out.** `auth.logout()`
already distinguishes three outcomes - `"revoked"`, `"already-invalid"`,
`"no-token"` - and `_cmd_logout` reports each one distinctly rather than
collapsing them into one "done" message. The dangerous direction is a failed
revoke (`auth.RevokeError` or `exc.ApiError`, both left to propagate by
`auth.logout()` so the local file is never cleared out from under a
credential that might still be live): `_cmd_logout` re-raises both, with the
same guidance text a caller needs, rather than catching them into a returned
string - a caught failure would read as failure in the text while
`_on_call_tool` still reported `is_error=False`, the exact protocol-level
inversion this design avoids. Getting this backwards, at either the text or
the protocol level, would tell a user their live credential is gone when it
is not. `logout` is annotated
`read_only_hint=False, destructive_hint=True, idempotent_hint=True,
open_world_hint=True` - honestly destructive (it revokes a real credential),
but idempotent (calling it again after success just finds `"no-token"`) and
open-world (it calls Zendesk's revoke endpoint), which is what ADR-017 argues
does not justify hiding the tool.

**`authenticate` never offers the paste fallback.** `auth.login(paste=True)`
prints a URL and then reads the pasted redirect back from `sys.stdin` - safe
for a human at a terminal, but `sys.stdin` under stdio MCP is the same
channel carrying inbound JSON-RPC, symmetric with why this module never
writes to `sys.stdout`. `_cmd_authenticate` always calls `auth.login` with
`paste=False`: the loopback-listener path only opens a browser and waits on a
local socket, touching neither stdio stream.

**Identity fields are the one place this module's own auth tools return
requester-influenced text.** `auth_status` and `logout`'s outcomes are almost
entirely this library's own diagnostics (statuses, expiries, scope names) and
stay unwrapped for the same reason the truncation warning does. `whoami`'s
`name`/`email`, called from `_cmd_authenticate`, are the exception: they are
whatever the authenticated Zendesk account holder set them to, so they pass
through `_untrusted.wrap` individually before being reported - the identity
is trustworthy (it is genuinely who this credential belongs to), but the
*string value* of a name field is still requester-set text, and the same rule
that governs a ticket's `subject` applies to it.

`tokens.scope` (reported by both `_cmd_authenticate` and `_cmd_auth_status`,
unwrapped) is, strictly, also a string read out of a response body - the OAuth
token endpoint's `scope` field, not something this library invented. It stays
unwrapped deliberately and is not a gap in the claim above: unlike a name or a
ticket subject, it is drawn from a small, closed vocabulary of scope keywords
this project itself requested in `login()`'s consent screen (`read`,
`tickets:write`, ...), never free text a requester can set - the same
treatment `_untrusted._is_machine_set` already gives an enum field like
`status` or `role`. Low risk either way (it is not requester-authorable), but
worth stating precisely rather than leaving "requester-influenced" to imply
more than the code actually does.

**Synchronous dispatch, deliberately.** `call_tool_sync` takes a tool name and
already-parsed arguments and returns a plain string - no `asyncio`, no MCP
types. That is what lets `tests/test_server.py` exercise dispatch, the
unknown-tool refusal and the untrusted-wrap guarantee without running an
event loop. `_on_call_tool` is the thin async adapter the MCP `Server` (with
one running) actually calls; the only work IT does beyond `call_tool_sync`
is catching this library's own typed errors (`exceptions.ZendeskError`, a
bad tool name's `ValueError`) and turning them into an `is_error` tool result
instead of letting them tear down the session - a policy refusal or an
upstream 404 is an ordinary, expected outcome for a tool call, not a reason
to crash the server. The two exception types are NOT handled identically:
a `ZendeskError`'s message is Zendesk's own HTTP error text (`_errors.py`'s
`parse_error()` builds it from the response body) and gets wrapped through
`_untrusted` like any other vendor-sourced string; a `ValueError` is this
library's own diagnostic and is left unwrapped, for the same reason the
truncation warning below sits outside the markers - wrapping our own text
would invite the model to discount it. See the provenance comment at
`_on_call_tool`'s `except` clauses for the one-line rule.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__, _untrusted, auth, backend
from . import exceptions as exc
from ._connect import connect
from .client import ZendeskClient
from .policy import TICKET_READ

__all__ = [
    "AUTH_TOOLS",
    "E1_CAPABILITIES",
    "INSTRUCTIONS",
    "READ_TOOLS",
    "TOOLS",
    "build_server",
    "call_tool_sync",
    "main",
]

#: API-SURFACE.md §5.4g: `ListTicketComments` never returns more than this many
#: comments in one call - `client.ZendeskClient.list_comments` takes no paging
#: parameter, so a result at the cap is the one shape that can mean either
#: "the whole conversation" or "the oldest slice of a much longer one," and
#: `call_tool_sync` cannot tell which without a second, cursor-paginated
#: request this tool does not make. See the module docstring's truncation note.
_COMMENTS_PAGE_CAP = 100

_TRUNCATION_WARNING = (
    "NOTE: this ticket has at least {count} comments, which is Zendesk's per-page cap for this "
    "endpoint (API-SURFACE.md §5.4g). Comments are sorted oldest-first, so if there are more than "
    "{count}, the newest ones - the ones most likely to carry the current state of the "
    "conversation - are NOT included below. Treat this as a possibly-incomplete history, not the "
    "full thread.\n\n"
)

#: Rung E1 (whole-project design §5): "triage the live queue; propose
#: everything, change nothing." `TICKET_READ` alone - not
#: `policy.PROFILES["readonly"]`, which also grants `HC_READ`/`PEOPLE_READ`/
#: `REPORTING_READ`/`ADMIN_READ` that none of `get_ticket`/`search_tickets`/
#: `list_comments` exercises - is the minimum authority this file's tools
#: actually need, matching their honest `readOnlyHint=True` annotation
#: exactly rather than "at least as much." No capability here ends in
#: anything but `.read`, and none names `write`/`reply`/`close`/`solve` -
#: `test_the_server_requests_only_read_capabilities` pins both properties.
#: A write tool registered here by mistake still could not be reached: the
#: gate in `policy.py` refuses any capability this set does not grant,
#: independent of what `TOOLS` happens to list. Built from `policy.TICKET_READ`
#: rather than the literal `"ticket.read"` so the two can never drift apart.
E1_CAPABILITIES: frozenset[str] = frozenset({TICKET_READ})


def _client() -> ZendeskClient:
    """A thin indirection so tests substitute a fake client here.

    Every real call goes through `connect()`, which reads
    `CSA_ZENDESK_SUBDOMAIN` and the OAuth token store itself - nothing in this
    module holds a credential or constructs a `ZendeskClient` any other way.
    Connects with `capabilities=E1_CAPABILITIES` rather than
    `profile="readonly"`: a named profile is a convenience for a caller
    composing several capabilities, and this server only ever needs the one.
    """
    return connect(capabilities=E1_CAPABILITIES)


_TICKET_ID_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ticket_id": {
            "type": "integer",
            "description": "The numeric Zendesk ticket id.",
        },
    },
    "required": ["ticket_id"],
    "additionalProperties": False,
}

_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "A Zendesk search query, e.g. `status:open`. `type:ticket` is always appended by "
                "this tool - no need to write it, and writing a different `type:` yourself only "
                "narrows the result set to nothing rather than reaching another record type."
            ),
        },
        "page": {
            "type": "integer",
            "minimum": 1,
            "description": "1-based page number for offset paging. Defaults to 1.",
        },
        "per_page": {
            "type": "integer",
            "minimum": 1,
            "description": "Results per page for offset paging. Defaults to 25.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

#: The three read tools this server exposes. Scoped by tests independently of
#: `TOOLS` below - see the module docstring's note on why the two names exist.
READ_TOOLS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name="get_ticket",
        description="Fetch one Zendesk ticket by id, as the full raw ticket envelope.",
        input_schema=_TICKET_ID_SCHEMA,
        annotations=mcp_types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
    ),
    mcp_types.Tool(
        name="search_tickets",
        description=(
            "Search Zendesk tickets. The query is always constrained to type:ticket - even a "
            "query that names a different type (e.g. type:user) matches nothing, rather than "
            "returning that other record type - since this tool grants no authority over people "
            "or organization records."
        ),
        input_schema=_SEARCH_SCHEMA,
        annotations=mcp_types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
    ),
    mcp_types.Tool(
        name="list_comments",
        description=(
            "List a ticket's comments, oldest first. Zendesk caps this at 100 comments per call; "
            "a ticket with more is reported as possibly truncated (its newest comments omitted)."
        ),
        input_schema=_TICKET_ID_SCHEMA,
        annotations=mcp_types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
    ),
]

#: None of the three auth-lifecycle tools takes an argument - see the module
#: docstring's `AUTH_TOOLS` note for why each is a plain, no-input action.
_NO_ARGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

#: `authenticate`, `auth_status`, `logout` - reachable at every rung by
#: design (ADR-017, and see the module docstring). Annotated honestly rather
#: than by copying a neighbour's annotation: `authenticate` writes a
#: credential file and talks to Zendesk's OAuth server but destroys nothing;
#: `auth_status` only reads the local file, with no network call at all;
#: `logout` is the one destructive, idempotent, open-world write in this
#: server.
AUTH_TOOLS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name="authenticate",
        description=(
            "Run the OAuth login flow and store the resulting credential. Opens a browser for "
            "the user to sign in, then reports the authenticated identity and granted scope - "
            "never the token itself. This call blocks for up to 5 minutes waiting for the user "
            "to complete sign-in in the browser - tell the user to check for a new browser tab "
            "while you wait, rather than treating a long-running call as stuck. Call this "
            "whenever another tool reports the server is not authorized."
        ),
        input_schema=_NO_ARGS_SCHEMA,
        annotations=mcp_types.ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    ),
    mcp_types.Tool(
        name="auth_status",
        description=(
            "Report whether a stored credential exists, the token file's path, a human-readable "
            "expiry, and the granted scope. Makes no network call and never returns the token "
            "itself."
        ),
        input_schema=_NO_ARGS_SCHEMA,
        annotations=mcp_types.ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    ),
    mcp_types.Tool(
        name="logout",
        description=(
            "Revoke the stored credential on Zendesk's side, then delete the local token file. "
            "Revoking also invalidates the paired refresh token, so this is a complete logout. "
            "Safe to call even when already logged out."
        ),
        input_schema=_NO_ARGS_SCHEMA,
        annotations=mcp_types.ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    ),
]

#: `READ_TOOLS + AUTH_TOOLS`, an ordinary concatenation - reassigning the
#: module-level name Task 5 defined, not shadowing it, so `srv.TOOLS` means
#: the same thing to every caller regardless of which task's code they read.
TOOLS: list[mcp_types.Tool] = READ_TOOLS + AUTH_TOOLS

#: Threaded into `build_server()`. The second sentence is the load-bearing
#: one - see `csa-google-workspace`'s identical precedent in the module
#: docstring: without it a model burns turns retrying a call that cannot
#: succeed, or starts grepping the filesystem for a credential file.
INSTRUCTIONS = (
    "IF A TOOL REPORTS THAT THE SERVER IS NOT AUTHORIZED: call the `authenticate` tool, which "
    "runs the OAuth login flow and opens a browser for the user to sign in. Do not search the "
    "filesystem for a credential file and do not retry other tools until authorization "
    "completes. Call `auth_status` at any time to see whether a stored credential exists, its "
    "expiry, and its granted scope, with no network call. Call `logout` to revoke the stored "
    "credential; it is safe to call even when already logged out, and the only way back is a "
    "fresh `authenticate` call."
)


def _human_expiry(expires_at: float) -> str:
    """A short, human-readable statement of a token's remaining lifetime.

    Deliberately simpler than `cli.py`'s own `_human_expiry` (which renders
    two units of precision for an operator staring at a terminal): this
    module's `auth_status` tool only needs to distinguish "expired" from
    "expires in about N hours," and importing a private helper out of
    `cli.py` would tie this module to a leaf entry point that documents
    itself as "a door into OAuth, nothing more" - not a library other modules
    reach into.
    """
    remaining = expires_at - time.time()
    if remaining <= 0:
        return "expired"
    return f"expires in about {remaining / 3600:.1f} hours"


def _cmd_authenticate() -> str:
    """Run the OAuth flow synchronously, inside this tool call, and report
    the resulting identity - never the token.

    Always `paste=False`: the loopback-listener path only opens a browser and
    waits on a local socket, touching neither `sys.stdin` nor `sys.stdout` -
    unlike `paste=True`, which reads the pasted redirect back from
    `sys.stdin`, the same channel carrying inbound JSON-RPC under stdio MCP.
    `CSA_ZENDESK_SCOPES` is read the same way `cli.py`'s `_cmd_login` reads
    it - the one browser consent screen a human sees - defaulting to `read`.

    `auth.whoami()`'s `name`/`email` are wrapped individually before being
    reported: they are genuinely who this credential belongs to, but the
    string VALUE of a name field is still requester-set text, exactly like a
    ticket's `subject` - see the module docstring's note on this.
    """
    scopes = tuple(os.environ.get("CSA_ZENDESK_SCOPES", "read").split())
    tokens = auth.login(scopes=scopes, open_browser=True, paste=False)
    try:
        identity = auth.whoami()
    except auth.NotAuthenticated as e:
        # Important 6 (final whole-branch review): this USED TO catch and
        # *return* the failure text, so `_on_call_tool` built an
        # `is_error=False` result for a session that is not actually
        # authenticated - a token that answers 200 with an Anonymous user
        # object is exactly the case `whoami` exists to detect, and returning
        # instead of raising threw that detection away at the protocol level,
        # one function away from the identical inversion `_cmd_logout` was
        # already rewritten to avoid (see that function's docstring). A host
        # keying state off `is_error` would record this session as
        # authenticated when it is not. Re-raising (keeping the "a token was
        # written" guidance, chained with `from e`) lets `_on_call_tool`'s
        # `_NEVER_WRAP` handling set `is_error=True` the same way every other
        # auth failure in this server does; `auth.NotAuthenticated` is
        # already in that tuple, and its message is this library's own
        # diagnostic - a status code or "answered with an Anonymous user
        # object", never Zendesk response-body text - so it stays unwrapped.
        raise auth.NotAuthenticated(
            f"A token was written (granted scope: {tokens.scope}), but the identity check just after login failed: {e}"
        ) from e
    lines = [f"Authenticated. Granted scope: {tokens.scope}."]
    name = identity.get("name")
    if isinstance(name, str) and name:
        lines.append("Name: " + _untrusted.wrap(name, source="auth-identity.name"))
    email = identity.get("email")
    if isinstance(email, str) and email:
        lines.append("Email: " + _untrusted.wrap(email, source="auth-identity.email"))
    return "\n".join(lines)


def _cmd_auth_status() -> str:
    """What is on disk right now - no network call, and never the token."""
    tokens = auth.read()
    if tokens is None:
        return "Not authenticated: no token file on disk. Call the `authenticate` tool to log in."
    return f"Authenticated. Token file: {auth.token_path()}. {_human_expiry(tokens.expires_at)}. Scope: {tokens.scope}."


def _cmd_logout() -> str:
    """Revoke the stored credential, reporting each of `auth.logout()`'s
    three outcomes distinctly, and treating a failed revoke as a genuine
    failure - never as "logged out," at either level this response has.

    `auth.RevokeError` and `exc.ApiError` are the two ways `auth.logout()`
    lets a failed revoke propagate rather than returning - both mean the
    credential may still be live and the local file was deliberately left in
    place, so a caller can retry or revoke it by hand. **Re-raised, not
    swallowed into a returned string**: an earlier version of this function
    caught both and returned a plain "Logout failed: ..." string, which reads
    as failure to a model but reports success at the MCP protocol level -
    `call_tool_sync` returning normally means `_on_call_tool` builds an
    `is_error=False` result regardless of what the text says. A host that
    keys retry or UI behaviour off that flag, which is the mechanism MCP
    provides for exactly this, would treat a failed logout as a successful
    one - the dangerous direction: a user told they are logged out while a
    live 90-day credential remains on Zendesk's side is worse off than one
    told the logout failed. Re-raising (with the same guidance text folded
    in, and chained with `from e` so the original diagnostic survives) lets
    `_on_call_tool`'s existing exception handling set `is_error=True` the
    same way every other tool failure in this server does; `RevokeError`'s
    message is never wrapped (`_NEVER_WRAP`, at `_on_call_tool`) since it
    never carries text from a Zendesk response body, while `exc.ApiError` -
    a mixed type - defaults to wrapped, per that same comment.
    """
    try:
        outcome = auth.logout()
    except auth.RevokeError as e:
        raise auth.RevokeError(
            f"Logout failed: {e} The credential may still be live on Zendesk's side, and the "
            f"local token file was left in place on purpose. Try again, or revoke it by hand in "
            f"Zendesk Admin Center (Apps and integrations › APIs › OAuth clients)."
        ) from e
    except exc.ApiError as e:
        raise exc.ApiError(
            f"Logout failed: {e} The credential may still be live on Zendesk's side, and the "
            f"local token file was left in place on purpose. Try again, or revoke it by hand in "
            f"Zendesk Admin Center (Apps and integrations › APIs › OAuth clients).",
            status=e.status,
        ) from e
    if outcome == "no-token":
        return "Nothing to log out of: no token file was on disk."
    if outcome == "already-invalid":
        return "Logged out. The stored credential was already invalid; the local file has been cleared."
    # The only outcome string left once "no-token" and "already-invalid" are handled.
    return "Logged out: the credential was revoked server-side and the local file cleared."


def call_tool_sync(name: str, arguments: dict[str, Any]) -> str:
    """Dispatch one tool call and return its fully-wrapped, model-safe text.

    Synchronous and free of any MCP type - see the module docstring for why
    that is what makes this testable without an event loop. Raises
    `ValueError` for a tool name this server does not register, and whatever
    `_client()`'s `ZendeskClient` raises (an `exceptions.ZendeskError`
    subclass) for anything the backend or the policy refuses - both are left
    to the caller to handle; `_on_call_tool` is where this server does that.

    The three `AUTH_TOOLS` names are dispatched first, before the unknown-name
    check below: they never call `_client()` (no policy-gated Zendesk client
    is involved in logging in, checking status, or logging out - see
    `ADR-017`). `authenticate` and `auth_status` always return a plain string
    already safe to hand back, exactly like the read-tool branches below;
    `logout` returns one on any of its three ordinary outcomes but RAISES
    (`auth.RevokeError` or `exc.ApiError`) on a failed revoke, deliberately -
    see `_cmd_logout`'s docstring for why a caught-and-returned failure string
    would report success at the MCP protocol level.

    The unknown-name check runs BEFORE `_client()` is ever called: `_client()`
    calls `connect()`, which can itself raise (`CSA_ZENDESK_SUBDOMAIN` unset,
    no stored credential, ...) - a caller who passed a bad tool name should
    see that mistake, not a connection failure that has nothing to do with
    what they asked for.
    """
    if name == "authenticate":
        return _cmd_authenticate()
    if name == "auth_status":
        return _cmd_auth_status()
    if name == "logout":
        return _cmd_logout()
    if name not in {"get_ticket", "search_tickets", "list_comments"}:
        raise ValueError(f"unknown tool: {name!r}")
    client = _client()
    if name == "get_ticket":
        envelope = client.get_ticket(ticket_id=arguments["ticket_id"])
        return json.dumps(_untrusted.wrap_ticket(envelope), indent=2)
    if name == "search_tickets":
        page = arguments.get("page", 1)
        per_page = arguments.get("per_page", 25)
        envelope = client.search_tickets(query=arguments["query"], page=page, per_page=per_page)
        return json.dumps(_untrusted.wrap_search(envelope), indent=2)
    # The only name the membership check above still lets through here.
    envelope = client.list_comments(ticket_id=arguments["ticket_id"])
    text = json.dumps(_untrusted.wrap_comments(envelope), indent=2)
    comments = envelope.get("comments")
    count = len(comments) if isinstance(comments, list) else 0
    if count >= _COMMENTS_PAGE_CAP:
        text = _TRUNCATION_WARNING.format(count=count) + text
    return text


async def _on_list_tools(
    context: Any,
    params: mcp_types.PaginatedRequestParams | None,
) -> mcp_types.ListToolsResult:
    return mcp_types.ListToolsResult(tools=TOOLS)


#: `exceptions.ZendeskError` subclasses whose message is, at EVERY raise site
#: in this package, this library's own prose - never text taken from a Zendesk
#: HTTP response body, and never text taken from any other external input.
#: Enumerated by reading every `raise` of each type across the package, not
#: assumed from the class hierarchy (a hierarchy that was never designed to
#: encode provenance can't be trusted to sort by it - see Task 6's fix report
#: for the full audit):
#:
#:   - `exc.PolicyError`: `policy.py`/`tools.py` build every message from the
#:     capability name and profile this process itself holds - a policy
#:     refusal names what WE will not grant, never anything Zendesk sent.
#:   - `exc.InvalidPath`: `_http.py` raises this before a request is ever
#:     built or sent, from a `path` this codebase itself constructed
#:     (`f"/api/v2/tickets/{ticket_id}"` and the like) - there is no response
#:     body in play yet.
#:   - `exc.SearchLimitExceeded`: both raise sites (`backend.py`'s pre-flight
#:     check, `_errors.py`'s 422 branch) use a fixed sentence naming the
#:     documented 1000-result ceiling - neither interpolates anything Zendesk
#:     sent back.
#:   - `backend.EmptyWrite`: both raise sites (`backend.py`'s `_refuse_an_
#:     empty_assignment`, `_refuse_an_empty_update`) use a fixed sentence
#:     naming what the call needs (`assignee_id`/`group_id`, or a non-empty
#:     `fields`) - a local, pre-flight refusal on this process's own
#:     arguments, before any request is built or sent, the same shape as
#:     `exc.InvalidPath` and `exc.SearchLimitExceeded` just above.
#:   - `exc.RateLimited`, `exc.ServiceUnavailable`: `_errors.py` builds both
#:     from a fixed string ("Zendesk rate limit reached" / "...likely
#:     maintenance"); `retry_after` is an int off the `Retry-After` header,
#:     never message text. `_http.py`'s `_budget_exhausted` only appends more
#:     of this module's own prose to that same fixed string.
#:   - `auth.NotAuthorised`: every site (`_connect.py`, `_flow.py`) reports a
#:     missing environment variable or a missing token file - configuration
#:     state on this machine, before any request is sent.
#:   - `auth.TokenAlreadyInvalid`: one site, `_flow.revoke()` - reports the
#:     revoke call got a 401, naming only the status code.
#:   - `auth.TokenFileError`: `_store.py` reports this process's own token
#:     file's mode/corruption/symlink state - never a response body.
#:   - `auth.AuthExchangeError`: `_flow.py` reports either a bare status code
#:     ("Zendesk refused the grant (HTTP {status})") or a structural
#:     complaint about a 200 response missing an expected field, naming the
#:     Python exception TYPE that was raised, never body content.
#:   - `auth.NotAuthenticated`: `whoami.py` reports a status code or "answered
#:     with an Anonymous user object" - never text out of the body.
#:   - `auth.RevokeError`: `_flow.revoke()`'s one site reports a bare status
#:     code, same shape as `AuthExchangeError` above.
#:
#: Every OTHER `exc.ZendeskError` subclass falls into one of two remaining
#: cases, both of which `_on_call_tool`'s `except exc.ZendeskError` branch
#: below wraps (the safe default):
#:
#:   - Unconditionally vendor-derived: `exc.PlanBoundary`, `exc.
#:     EndpointNotAvailable`, `exc.NotFound`, `exc.ValidationError`, `exc.
#:     PaginationError`, `auth.ScopeError` - each interpolates `message`/
#:     `problems`/granted-scope text taken directly from a Zendesk response
#:     body, at every production raise site (`backend.py`'s two bare
#:     `NotFound`s are `FakeBackend`-only, never reached through `ApiBackend`).
#:   - Genuinely MIXED: `exc.ApiError` is raised BOTH with this library's own
#:     connectivity/shape prose (a dozen sites across `_http.py`, `whoami.py`,
#:     `_flow.py` - "could not reach Zendesk", "not a JSON object") AND, via
#:     `_errors.parse_error()`, with `message` interpolated straight out of a
#:     Zendesk error body. `auth.CallbackError` is similarly mixed: most of
#:     its messages are fixed prose, but two (`_callback.py`'s "the callback
#:     arrived on an unexpected path" and "Zendesk refused the authorization")
#:     splice in text taken verbatim from whatever request hit the local OAuth
#:     loopback socket - untrusted, though not necessarily Zendesk's, since
#:     nothing but the `state` parameter (checked separately) stops an
#:     unrelated local process from sending that request instead.
#:     A class-level check cannot tell either mixed type's instances apart, so
#:     they default to the safe side - wrapped - per `_untrusted.py`'s own
#:     doctrine ("when in doubt, wrap"): over-wrapping an occasional
#:     all-ours `ApiError`/`CallbackError` message costs a reader some trust
#:     in genuinely-ours text; under-wrapping a vendor-derived one is the
#:     vulnerability `_untrusted` exists to close. Splitting each by
#:     provenance at the raise site (an explicit marker, not a class) is the
#:     correct fix and is deliberately deferred for these two - see TODO.md
#:     E22.
#:   - `exc.CredentialsRejected` WAS in this mixed category (and TODO.md E22
#:     described it as such) until fix wave I7: it is genuinely mixed across
#:     its two raise sites - `_http.py`'s empty-access-token check is all
#:     ours, `_errors.parse_error()`'s 401 branch splices in Zendesk's own
#:     `message` - but unlike `ApiError`/`CallbackError` it carries the ONE
#:     remedy sentence an operator most needs to read as authoritative (call
#:     `authenticate` to fix it), so defaulting the whole thing to wrapped
#:     would bury that sentence under the same discount-this-content framing
#:     the vendor text needs. Rather than adding it here (which would
#:     under-wrap the vendor-derived instances) or leaving it wrapped
#:     wholesale (which would bury the remedy), `CredentialsRejected` now
#:     carries its remedy as a separate, never-interpolated attribute
#:     (`exceptions.CredentialsRejected.remedy`) and gets its own branch in
#:     `_on_call_tool`, below, instead of falling through to the generic
#:     `except exc.ZendeskError` handling every other mixed or vendor-derived
#:     type uses.
_NEVER_WRAP: tuple[type[exc.ZendeskError], ...] = (
    exc.PolicyError,
    exc.InvalidPath,
    exc.SearchLimitExceeded,
    backend.EmptyWrite,
    exc.RateLimited,
    exc.ServiceUnavailable,
    auth.NotAuthorised,
    auth.TokenAlreadyInvalid,
    auth.TokenFileError,
    auth.AuthExchangeError,
    auth.NotAuthenticated,
    auth.RevokeError,
)


async def _on_call_tool(
    context: Any,
    params: mcp_types.CallToolRequestParams,
) -> mcp_types.CallToolResult:
    # Provenance rule for the branches below: THEIR TEXT IS WRAPPED, OURS IS
    # NOT. A bad tool name (ValueError) is always this library's own
    # diagnostic text - wrapping it would invite the model to discount our own
    # error, the same reason the truncation warning above sits outside the
    # markers. `_NEVER_WRAP` (see its own comment, just above) is the
    # enumerated set of `ZendeskError` subclasses that are ALSO always this
    # library's own prose, by audit rather than by class hierarchy - a bad
    # tool name's `ValueError` is not itself in that tuple only because it
    # is not a `ZendeskError` at all, so it needs its own branch to reach the
    # same "never wrap" outcome. `exc.CredentialsRejected` gets its own branch,
    # between `_NEVER_WRAP` and the generic case, because it is genuinely
    # mixed per-INSTANCE rather than per-class (see `_NEVER_WRAP`'s comment):
    # its `remedy` attribute, when set, is this library's own prose and stays
    # unwrapped even though `str(e)` - Zendesk's own text - is wrapped right
    # alongside it. Every other `ZendeskError` - vendor-derived or genuinely
    # mixed at the class level - is wrapped whole: `_untrusted`'s own rule
    # applies with no exemption, "wrap everything, then name the
    # exceptions... when in doubt, wrap." Every branch returns
    # `is_error=True`; only whether (and how much of) the content is wrapped
    # differs.
    try:
        text = call_tool_sync(params.name, params.arguments or {})
    except ValueError as e:
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=str(e))],
            is_error=True,
        )
    except _NEVER_WRAP as e:
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=str(e))],
            is_error=True,
        )
    except exc.CredentialsRejected as e:
        wrapped = _untrusted.wrap(str(e), source=f"zendesk-error.{params.name}")
        content = wrapped if e.remedy is None else f"{wrapped}\n\n{e.remedy}"
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=content)],
            is_error=True,
        )
    except exc.ZendeskError as e:
        wrapped = _untrusted.wrap(str(e), source=f"zendesk-error.{params.name}")
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=wrapped)],
            is_error=True,
        )
    return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=text)])


def build_server() -> Server[None]:
    """Construct the MCP `Server`, wired to `TOOLS` and `INSTRUCTIONS`.

    Building the server registers handlers; it makes no network call and
    starts no I/O loop - `main()` is what actually serves stdio.
    """
    return Server(
        "csa-zendesk",
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )


async def _serve() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> int:
    """Entry point for the `csa-zendesk-mcp` console script.

    Serves one stdio connection - the lifetime of the process an MCP client
    launches - and returns 0 on a clean shutdown. Any failure, including an
    interactive `KeyboardInterrupt`, is left to propagate rather than caught
    and printed here: this module never touches stdout (see the module
    docstring), and inventing a stderr message for a case no test exercises
    is exactly the kind of behaviour-to-fill-a-branch this project's
    coverage gate exists to refuse (CLAUDE.md). An operator diagnosing a dead
    server reads the client's own logs or the interpreter's traceback on
    stderr, same as any other Python process.
    """
    asyncio.run(_serve())
    return 0
