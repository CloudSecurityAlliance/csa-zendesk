import asyncio

import pytest

from csa_zendesk import server as srv


def test_the_read_tools_are_exactly_the_three_read_tools():
    # Asserts on READ_TOOLS, not TOOLS - TOOLS grows in Task 6 to include the
    # auth-lifecycle tools, and this property must stay true regardless. (The
    # `TOOLS == READ_TOOLS` equality this test originally also asserted held
    # only because Task 6 had not yet landed; Task 6's own
    # `test_tools_is_read_tools_plus_auth_tools`, below, is the corrected
    # version of that check - `TOOLS == READ_TOOLS + AUTH_TOOLS`.)
    names = {t.name for t in srv.READ_TOOLS}
    assert names == {"search_tickets", "get_ticket", "list_comments"}


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

    from csa_zendesk import _untrusted

    params = mcp_types.CallToolRequestParams(name="delete_everything", arguments={})
    result = asyncio.run(srv._on_call_tool(None, params))
    text = result.content[0].text
    assert result.is_error is True
    assert text == "unknown tool: 'delete_everything'"
    # Ours, not wrapped: the provenance rule at _on_call_tool's except clauses
    # ("their text is wrapped, ours is not") means a ValueError - always this
    # library's own diagnostic, never vendor-sourced - comes back exactly as
    # raised, with no untrusted markers at all.
    assert _untrusted.MARKER_OPEN not in text


def test_on_call_tool_wraps_a_zendesk_error_as_untrusted_vendor_text(monkeypatch):
    # `srv.exc`, not a fresh `from csa_zendesk import exceptions` here: this
    # test can run after test_public_api.py's import-time guard, which
    # deliberately deletes and re-imports every csa_zendesk module (including
    # `exceptions`) to observe a cold start. A fresh import in THIS test body
    # would fetch that later module object, while `server.py`'s own
    # `except exc.ZendeskError` still closes over whichever module object was
    # current when `server` was first imported - two distinct classes named
    # `NotFound` that `isinstance` correctly treats as unrelated. Raising
    # through `srv.exc` uses the exact class `server.py` itself catches
    # against, independent of import order elsewhere.
    from csa_zendesk import _untrusted

    # A ZendeskError's message is built from Zendesk's own HTTP error body
    # (`_errors.parse_error()`) - vendor text, exactly like a ticket subject or
    # comment body. Carrying a marker-shaped substring here proves the wrap is
    # real, not decorative: if this ever again reached the model unwrapped,
    # `_untrusted.MARKER_OPEN` would appear twice - once genuine, once forged.
    class _Client:
        def get_ticket(self, *, ticket_id):
            raise srv.exc.NotFound(f"upstream said: {_untrusted.MARKER_OPEN} nested")

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    from mcp import types as mcp_types

    params = mcp_types.CallToolRequestParams(name="get_ticket", arguments={"ticket_id": 1})
    result = asyncio.run(srv._on_call_tool(None, params))
    text = result.content[0].text
    assert result.is_error is True
    assert "upstream said" in text
    # Wrapped: this wrap() call's own markers frame the whole message.
    assert text.startswith(_untrusted.MARKER_OPEN)
    assert text.endswith(_untrusted.MARKER_CLOSE)
    # Neutralised: the only literal MARKER_OPEN substring anywhere in the
    # text is the genuine one this wrap() call produced at the very start -
    # the error message's own attempt at a marker did not survive as `<`/`>`.
    assert text.count(_untrusted.MARKER_OPEN) == 1
    assert "‹‹‹" in text


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


# --- Task 6: authentication from inside the server -------------------------


def test_the_auth_tools_are_exactly_authenticate_auth_status_and_logout():
    assert {t.name for t in srv.AUTH_TOOLS} == {"authenticate", "auth_status", "logout"}


def test_tools_is_read_tools_plus_auth_tools():
    assert srv.TOOLS == srv.READ_TOOLS + srv.AUTH_TOOLS


def test_logout_is_annotated_as_a_destructive_idempotent_open_world_write():
    (t,) = [t for t in srv.AUTH_TOOLS if t.name == "logout"]
    assert t.annotations.read_only_hint is False, t.name
    assert t.annotations.destructive_hint is True, t.name
    assert t.annotations.idempotent_hint is True, t.name
    assert t.annotations.open_world_hint is True, t.name


def test_authenticate_is_annotated_as_a_non_destructive_open_world_write():
    # Not read-only (it writes a credential file) and not destructive
    # (nothing is lost) - the opposite of logout's annotation, per the brief:
    # "annotate logout honestly, not by copying authenticate's annotation."
    (t,) = [t for t in srv.AUTH_TOOLS if t.name == "authenticate"]
    assert t.annotations.read_only_hint is False, t.name
    assert t.annotations.destructive_hint is False, t.name
    assert t.annotations.open_world_hint is True, t.name


def test_auth_status_is_annotated_as_a_read_only_local_only_check():
    (t,) = [t for t in srv.AUTH_TOOLS if t.name == "auth_status"]
    assert t.annotations.read_only_hint is True, t.name
    assert t.annotations.destructive_hint is False, t.name
    assert t.annotations.open_world_hint is False, t.name  # no network call


def test_logout_never_returns_a_token(monkeypatch):
    monkeypatch.setattr(srv.auth, "logout", lambda: "revoked")
    out = srv.call_tool_sync("logout", {})
    assert "AT-" not in out and "RT-" not in out


def test_logout_reports_revoked(monkeypatch):
    monkeypatch.setattr(srv.auth, "logout", lambda: "revoked")
    out = srv.call_tool_sync("logout", {})
    assert "revoked" in out.lower()


def test_logout_reports_already_invalid_without_claiming_it_did_the_revoking(monkeypatch):
    monkeypatch.setattr(srv.auth, "logout", lambda: "already-invalid")
    out = srv.call_tool_sync("logout", {})
    assert "logged out" in out.lower()
    assert "already" in out.lower()


def test_logout_reports_no_token(monkeypatch):
    monkeypatch.setattr(srv.auth, "logout", lambda: "no-token")
    out = srv.call_tool_sync("logout", {})
    assert "nothing to log out of" in out.lower() or "no token" in out.lower()


def test_a_genuine_revoke_failure_is_reported_as_failure_not_as_logged_out(monkeypatch):
    # The dangerous direction: a failed revoke must never be reported as success.
    #
    # Raises through `srv.auth.RevokeError`, not a fresh `from csa_zendesk.auth
    # import _flow` (the brief's own snippet does the latter): test_public_api.py's
    # import-time guard deletes and reimports every csa_zendesk module, including
    # `auth` and `auth._flow`, to observe a cold start. When this file's tests run
    # after that guard in the full suite, a fresh import fetches the NEW module
    # object's `RevokeError`, while `_cmd_logout`'s own `except (auth.RevokeError,
    # ...)` still closes over whichever module object was current when `server`
    # was first imported - two distinct classes named `RevokeError` that `except`
    # correctly treats as unrelated, so the raised exception goes uncaught and the
    # test fails for a reason that has nothing to do with the behaviour under
    # test. Same trap, same fix, as `test_on_call_tool_wraps_a_zendesk_error_as_
    # untrusted_vendor_text`'s comment above.
    def _raise() -> str:
        raise srv.auth.RevokeError("revoke request failed")

    monkeypatch.setattr(srv.auth, "logout", _raise)
    out = srv.call_tool_sync("logout", {})
    assert "logged out" not in out.lower()
    assert "fail" in out.lower() or "may still be" in out.lower()


def test_a_transport_failure_during_logout_is_also_reported_as_failure(monkeypatch):
    # The other exception `auth.logout()` lets propagate - a network failure
    # reaching Zendesk's revoke endpoint, not a rejected revoke.
    def _raise() -> str:
        raise srv.exc.ApiError("could not reach the Zendesk OAuth revoke endpoint (ConnectError)")

    monkeypatch.setattr(srv.auth, "logout", _raise)
    out = srv.call_tool_sync("logout", {})
    assert "logged out" not in out.lower()
    assert "fail" in out.lower() or "may still be" in out.lower()


def test_auth_status_never_returns_a_token(monkeypatch):
    from csa_zendesk.auth import _store

    monkeypatch.setattr(
        srv.auth,
        "read",
        lambda: _store.Tokens(access_token="AT-SECRET", refresh_token="RT-SECRET", expires_at=9e9, scope="read"),
    )
    out = srv.call_tool_sync("auth_status", {})
    assert "AT-SECRET" not in out and "RT-SECRET" not in out
    assert "read" in out


def test_auth_status_when_logged_out_says_so_rather_than_failing(monkeypatch):
    monkeypatch.setattr(srv.auth, "read", lambda: None)
    out = srv.call_tool_sync("auth_status", {})
    assert "authenticate" in out.lower()


def test_auth_status_reports_an_unexpired_token_as_such(monkeypatch):
    from csa_zendesk.auth import _store

    monkeypatch.setattr(
        srv.auth,
        "read",
        lambda: _store.Tokens(access_token="AT-1", refresh_token="RT-1", expires_at=9e9, scope="read"),
    )
    out = srv.call_tool_sync("auth_status", {})
    assert "expires in" in out.lower()
    assert "expired" not in out.lower()


def test_auth_status_reports_an_expired_token_as_such(monkeypatch):
    from csa_zendesk.auth import _store

    monkeypatch.setattr(
        srv.auth,
        "read",
        lambda: _store.Tokens(access_token="AT-2", refresh_token="RT-2", expires_at=1.0, scope="read"),
    )
    out = srv.call_tool_sync("auth_status", {})
    assert "expired" in out.lower()
    assert "AT-2" not in out and "RT-2" not in out


def test_authenticate_reports_identity_and_scope_without_a_token(monkeypatch):
    from csa_zendesk import _untrusted
    from csa_zendesk.auth import _store

    def fake_login(*, scopes, open_browser, paste):
        assert paste is False  # never the stdin-reading paste fallback under stdio MCP
        assert open_browser is True
        return _store.Tokens(access_token="AT-SECRET", refresh_token="RT-SECRET", expires_at=9e9, scope="read")

    monkeypatch.setattr(srv.auth, "login", fake_login)
    monkeypatch.setattr(srv.auth, "whoami", lambda: {"id": 1, "name": "Jane Doe", "email": "jane@example.com"})
    out = srv.call_tool_sync("authenticate", {})
    assert "AT-SECRET" not in out and "RT-SECRET" not in out
    assert "read" in out
    assert "Jane Doe" in out
    assert "jane@example.com" in out
    # Identity fields are requester-set text and must be wrapped as untrusted,
    # even though the identity itself is trustworthy - see the module
    # docstring's note on this.
    assert _untrusted.MARKER_OPEN in out


def test_authenticate_reports_scope_when_identity_has_no_name_or_email(monkeypatch):
    from csa_zendesk.auth import _store

    monkeypatch.setattr(
        srv.auth,
        "login",
        lambda *, scopes, open_browser, paste: _store.Tokens(
            access_token="AT-X", refresh_token="RT-X", expires_at=9e9, scope="read"
        ),
    )
    monkeypatch.setattr(srv.auth, "whoami", lambda: {"id": 1})
    out = srv.call_tool_sync("authenticate", {})
    assert "authenticated" in out.lower()
    assert "Name:" not in out
    assert "Email:" not in out


def test_authenticate_reports_when_the_post_login_identity_check_fails(monkeypatch):
    from csa_zendesk.auth import _store

    monkeypatch.setattr(
        srv.auth,
        "login",
        lambda *, scopes, open_browser, paste: _store.Tokens(
            access_token="AT-X", refresh_token="RT-X", expires_at=9e9, scope="read"
        ),
    )

    def fake_whoami():
        raise srv.auth.NotAuthenticated("Zendesk rejected the credential")

    monkeypatch.setattr(srv.auth, "whoami", fake_whoami)
    out = srv.call_tool_sync("authenticate", {})
    assert "identity check" in out.lower()
    assert "AT-X" not in out and "RT-X" not in out


def test_auth_tools_never_call_the_policy_gated_client(monkeypatch):
    # authenticate/auth_status/logout are auth-lifecycle tools (ADR-017) -
    # none of them goes through `_client()`/`connect()`'s readonly profile.
    def _boom():
        raise AssertionError("an auth tool must not call _client()")

    monkeypatch.setattr(srv, "_client", _boom)
    monkeypatch.setattr(srv.auth, "read", lambda: None)
    monkeypatch.setattr(srv.auth, "logout", lambda: "no-token")
    assert "authenticate" in srv.call_tool_sync("auth_status", {}).lower()
    assert "nothing to log out of" in srv.call_tool_sync("logout", {}).lower()


def test_the_server_instructions_tell_the_model_not_to_retry_or_hunt_for_files():
    text = srv.INSTRUCTIONS.lower()
    assert "authenticate" in text
    assert "do not retry" in text
    assert "credential file" in text


def test_build_server_carries_the_instructions():
    server = srv.build_server()
    assert server.instructions == srv.INSTRUCTIONS
