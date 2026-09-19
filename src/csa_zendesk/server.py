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
No write tool is registered - the `readonly` capability profile this module
connects with would refuse one anyway (`policy.PROFILES["readonly"]` grants
only `*.read`), but a tool a model can see and cannot use is a worse
experience than one that is simply absent (task brief).

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

**Synchronous dispatch, deliberately.** `call_tool_sync` takes a tool name and
already-parsed arguments and returns a plain string - no `asyncio`, no MCP
types. That is what lets `tests/test_server.py` exercise dispatch, the
unknown-tool refusal and the untrusted-wrap guarantee without running an
event loop. `_on_call_tool` is the thin async adapter the MCP `Server` (with
one running) actually calls; the only work IT does beyond `call_tool_sync`
is catching this library's own typed errors (`exceptions.ZendeskError`, a
bad tool name's `ValueError`) and turning them into an `isError` tool result
instead of letting them tear down the session - a policy refusal or an
upstream 404 is an ordinary, expected outcome for a tool call, not a reason
to crash the server.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import _untrusted
from . import exceptions as exc
from ._connect import connect
from .client import ZendeskClient

__all__ = ["READ_TOOLS", "TOOLS", "build_server", "call_tool_sync", "main"]

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

#: The `readonly` profile (`policy.PROFILES["readonly"]`) grants exactly
#: `TICKET_READ` (plus `HC_READ`/`PEOPLE_READ`/`REPORTING_READ`/`ADMIN_READ`,
#: none of which any tool below exercises) - the minimum authority that backs
#: `get_ticket`/`search_tickets`/`list_comments`, matching the honest
#: `readOnlyHint=True` annotation every tool below carries. This module never
#: connects with a wider profile: a read-only tool surface backed by broader
#: authority would be an accident waiting for the next tool this file gains.
_PROFILE = "readonly"


def _client() -> ZendeskClient:
    """A thin indirection so tests substitute a fake client here.

    Every real call goes through `connect()`, which reads
    `CSA_ZENDESK_SUBDOMAIN` and the OAuth token store itself - nothing in this
    module holds a credential or constructs a `ZendeskClient` any other way.
    """
    return connect(profile=_PROFILE)


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
            "description": "A Zendesk search query, e.g. `type:ticket status:open`.",
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
        description="Search Zendesk tickets (and, for an unconstrained query, other record types).",
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

#: A plain list, not a re-derivation of `READ_TOOLS` - so a later task can
#: write `TOOLS = READ_TOOLS + AUTH_TOOLS` as an ordinary concatenation.
TOOLS: list[mcp_types.Tool] = READ_TOOLS


def call_tool_sync(name: str, arguments: dict[str, Any]) -> str:
    """Dispatch one tool call and return its fully-wrapped, model-safe text.

    Synchronous and free of any MCP type - see the module docstring for why
    that is what makes this testable without an event loop. Raises
    `ValueError` for a tool name this server does not register, and whatever
    `_client()`'s `ZendeskClient` raises (an `exceptions.ZendeskError`
    subclass) for anything the backend or the policy refuses - both are left
    to the caller to handle; `_on_call_tool` is where this server does that.

    The unknown-name check runs BEFORE `_client()` is ever called: `_client()`
    calls `connect()`, which can itself raise (`CSA_ZENDESK_SUBDOMAIN` unset,
    no stored credential, ...) - a caller who passed a bad tool name should
    see that mistake, not a connection failure that has nothing to do with
    what they asked for.
    """
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


async def _on_call_tool(
    context: Any,
    params: mcp_types.CallToolRequestParams,
) -> mcp_types.CallToolResult:
    try:
        text = call_tool_sync(params.name, params.arguments or {})
    except (ValueError, exc.ZendeskError) as e:
        # A bad tool name, a policy refusal, or an upstream failure is an
        # ordinary, expected tool outcome - reported to the model as a failed
        # tool call, not allowed to tear down the session.
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=str(e))],
            is_error=True,
        )
    return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=text)])


def build_server() -> Server[None]:
    """Construct the MCP `Server`, wired to the three read tools above.

    Building the server registers handlers; it makes no network call and
    starts no I/O loop - `main()` is what actually serves stdio.
    """
    return Server(
        "csa-zendesk",
        version="0.0.1",
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
