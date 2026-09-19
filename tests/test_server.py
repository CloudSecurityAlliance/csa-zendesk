import asyncio

import pytest

from csa_zendesk import server as srv


def test_the_read_tools_are_exactly_the_three_read_tools():
    # Asserts on READ_TOOLS, not TOOLS - TOOLS grows in Task 6 to include the
    # auth-lifecycle tools, and this property must stay true regardless.
    names = {t.name for t in srv.READ_TOOLS}
    assert names == {"search_tickets", "get_ticket", "list_comments"}
    assert srv.TOOLS == srv.READ_TOOLS


def test_every_read_tool_is_annotated_read_only_and_non_destructive():
    # Scoped to READ_TOOLS for the same reason: Task 6 adds `authenticate`
    # (not read-only) and `logout` (destructive), which would falsify this if
    # it quantified over TOOLS.
    #
    # `read_only_hint`/`destructive_hint`, not the brief's `readOnlyHint`/
    # `destructiveHint`: the `mcp>=1.0` the brief specifies resolves today to
    # mcp 2.x, whose `ToolAnnotations` is a pydantic model exposing snake_case
    # attributes - camelCase survives only as the (de)serialisation alias, so
    # `Tool(..., annotations=ToolAnnotations(readOnlyHint=True, ...))` still
    # builds one (construction accepts the alias), but reading it back as
    # `.readOnlyHint` raises `AttributeError`, verified live against the
    # installed version. Same drift class as the brief's stale `connect()`
    # signature and `_untrusted` names - an SDK version the brief predates.
    for t in srv.READ_TOOLS:
        assert t.annotations.read_only_hint is True, t.name
        assert t.annotations.destructive_hint is False, t.name


def test_every_tool_response_is_wrapped_as_untrusted(monkeypatch):
    from csa_zendesk import _untrusted

    class _Client:
        def get_ticket(self, *, ticket_id):
            return {"ticket": {"id": ticket_id, "subject": "s"}}

        def search_tickets(self, *, query, page=1, per_page=25):
            return {"results": [{"id": 1, "subject": "s"}], "count": 1}

        def list_comments(self, *, ticket_id):
            return {"comments": [{"id": 1, "body": "b", "public": True}]}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    for name, args in [
        ("get_ticket", {"ticket_id": 1}),
        ("search_tickets", {"query": "x"}),
        ("list_comments", {"ticket_id": 1}),
    ]:
        out = srv.call_tool_sync(name, args)
        assert _untrusted.MARKER_OPEN in out, name


def test_an_unknown_tool_name_is_an_error_not_a_crash():
    with pytest.raises(ValueError, match="unknown tool"):
        srv.call_tool_sync("delete_everything", {})


def test_nothing_in_the_server_module_writes_to_stdout(capsys):
    # stdout IS the JSON-RPC channel. This asserts at import and registration time.
    srv.build_server()
    assert capsys.readouterr().out == ""


# --- coverage for behaviour the brief's five tests above do not reach ------


def test_client_connects_with_the_readonly_profile(monkeypatch):
    # The one call every other test in this file replaces via
    # `monkeypatch.setattr(srv, "_client", ...)`, so it is exercised on its
    # own here instead: `_client()` must ask `connect()` for the `readonly`
    # profile specifically, matching every tool's honest
    # `readOnlyHint=True` annotation.
    seen = {}

    def fake_connect(*, profile=None, capabilities=None, transport=None):
        seen["profile"] = profile
        return "a-client"

    monkeypatch.setattr(srv, "connect", fake_connect)
    assert srv._client() == "a-client"
    assert seen == {"profile": "readonly"}


def test_search_tickets_honours_explicit_page_and_per_page(monkeypatch):
    from csa_zendesk import _untrusted

    seen = {}

    class _Client:
        def search_tickets(self, *, query, page=1, per_page=25):
            seen["query"], seen["page"], seen["per_page"] = query, page, per_page
            return {"results": [], "count": 0}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    out = srv.call_tool_sync("search_tickets", {"query": "status:open", "page": 2, "per_page": 10})
    assert seen == {"query": "status:open", "page": 2, "per_page": 10}
    assert out  # a JSON envelope was produced, even with no wrapped strings inside it
    assert _untrusted.MARKER_OPEN not in out  # nothing requester-authored to wrap here


def test_list_comments_at_the_page_cap_is_flagged_as_possibly_truncated(monkeypatch):
    class _Client:
        def list_comments(self, *, ticket_id):
            return {"comments": [{"id": i, "body": "b"} for i in range(srv._COMMENTS_PAGE_CAP)]}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    out = srv.call_tool_sync("list_comments", {"ticket_id": 1})
    assert "NOTE" in out
    assert "100" in out


def test_list_comments_under_the_page_cap_is_not_flagged(monkeypatch):
    class _Client:
        def list_comments(self, *, ticket_id):
            return {"comments": [{"id": 1, "body": "b"}]}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    out = srv.call_tool_sync("list_comments", {"ticket_id": 1})
    assert "NOTE" not in out


def test_on_list_tools_returns_the_tool_table():
    result = asyncio.run(srv._on_list_tools(None, None))
    assert result.tools == srv.TOOLS


def test_on_call_tool_wraps_a_successful_call(monkeypatch):
    from csa_zendesk import _untrusted

    class _Client:
        def get_ticket(self, *, ticket_id):
            return {"ticket": {"id": ticket_id, "subject": "s"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    from mcp import types as mcp_types

    params = mcp_types.CallToolRequestParams(name="get_ticket", arguments={"ticket_id": 1})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is False
    assert _untrusted.MARKER_OPEN in result.content[0].text


def test_on_call_tool_reports_an_unknown_tool_as_an_error_result_not_a_crash():
    from mcp import types as mcp_types

    params = mcp_types.CallToolRequestParams(name="delete_everything", arguments={})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    assert "unknown tool" in result.content[0].text


def test_on_call_tool_reports_a_zendesk_error_as_an_error_result_not_a_crash(monkeypatch):
    # `srv.exc`, not a fresh `from csa_zendesk import exceptions` here: this
    # test can run after test_public_api.py's import-time guard, which
    # deliberately deletes and re-imports every csa_zendesk module (including
    # `exceptions`) to observe a cold start. A fresh import in THIS test body
    # would fetch that later module object, while `server.py`'s own
    # `except (..., exc.ZendeskError)` still closes over whichever module
    # object was current when `server` was first imported - two distinct
    # classes named `NotFound` that `isinstance` correctly treats as
    # unrelated. Raising through `srv.exc` uses the exact class `server.py`
    # itself catches against, independent of import order elsewhere.
    class _Client:
        def get_ticket(self, *, ticket_id):
            raise srv.exc.NotFound("no such record (ticket 1)")

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    from mcp import types as mcp_types

    params = mcp_types.CallToolRequestParams(name="get_ticket", arguments={"ticket_id": 1})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    assert "no such record" in result.content[0].text


def test_on_call_tool_defaults_missing_arguments_to_an_empty_dict():
    from mcp import types as mcp_types

    # `arguments=None` is a real shape an MCP client can send for a tool that
    # takes no required arguments in principle; this server has none such
    # today, so this exercises the `or {}` fallback via the unknown-tool path
    # rather than inventing a real zero-argument tool to carry it.
    params = mcp_types.CallToolRequestParams(name="delete_everything", arguments=None)
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True


def test_main_runs_one_stdio_session_and_returns_zero(monkeypatch):
    calls = []

    async def fake_serve():
        calls.append(True)

    monkeypatch.setattr(srv, "_serve", fake_serve)
    assert srv.main() == 0
    assert calls == [True]


def test_serve_wires_build_server_to_stdio(monkeypatch):
    import contextlib

    built = []
    ran = []

    class _FakeServer:
        def create_initialization_options(self):
            return "init-options"

        async def run(self, read_stream, write_stream, initialization_options):
            ran.append((read_stream, write_stream, initialization_options))

    def fake_build_server():
        built.append(True)
        return _FakeServer()

    @contextlib.asynccontextmanager
    async def fake_stdio_server():
        yield ("read", "write")

    monkeypatch.setattr(srv, "build_server", fake_build_server)
    monkeypatch.setattr(srv, "stdio_server", fake_stdio_server)
    asyncio.run(srv._serve())
    assert built == [True]
    assert ran == [("read", "write", "init-options")]
