import asyncio

import pytest

from csa_zendesk import server as srv


def test_the_read_tools_are_exactly_the_four_read_tools():
    # Asserts on READ_TOOLS, not TOOLS - TOOLS grows to include the
    # auth-lifecycle tools and (this task) the write tools, and this property
    # must stay true regardless. (The `TOOLS == READ_TOOLS` equality this
    # test originally also asserted held only because neither had yet
    # landed; `test_tools_is_read_tools_plus_write_tools_plus_auth_tools`,
    # below, is the corrected version of that check.)
    #
    # Four, not three: `get_attachment` joined READ_TOOLS in the same task
    # that added the six WRITE_TOOLS (orchestrator amendment to that task's
    # brief) - it gates on TICKET_READ, the same capability the other three
    # need, so its annotation (read_only_hint=True) and its gate agree, and
    # it belongs in this set rather than among the tools that actually
    # write. A three-element pin here would go stale the moment
    # get_attachment was registered; the fourth element is the set
    # genuinely changing, not the test going vacuous (CLAUDE.md's own
    # distinction, and the amendment's).
    names = {t.name for t in srv.READ_TOOLS}
    assert names == {"search_tickets", "get_ticket", "list_comments", "get_attachment"}


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


def test_every_remaining_string_in_a_wrapped_response_is_marked_or_machine_set(monkeypatch):
    # Smaller item, final whole-branch review: replaces
    # `test_every_tool_response_is_wrapped_as_untrusted` (deleted - it only
    # asserted MARKER_OPEN appears SOMEWHERE in the output, and so does this
    # test, but a regression that wrapped `subject` while leaving every
    # `comment.body` or `via.source.from.address` raw would still pass THAT
    # assertion). This re-walks the actual JSON the server hands back and
    # checks EVERY string in it, recursively: each one either sits under a key
    # `_untrusted._is_machine_set` recognises (an id, a timestamp, an enum
    # machine code sets and reads) or carries a genuine `MARKER_OPEN` - there
    # is no third case. This is the highest-value test missing from this
    # branch before this fix wave.
    import json

    from csa_zendesk import _untrusted

    class _Client:
        def get_ticket(self, *, ticket_id):
            return {
                "ticket": {
                    "id": ticket_id,
                    "status": "open",
                    "subject": "help",
                    "via": {
                        "channel": "email",
                        "source": {"from": {"name": "Attacker Name", "address": "a@example.com"}},
                    },
                }
            }

        def search_tickets(self, *, query, page=1, per_page=25):
            return {"results": [{"id": 1, "result_type": "ticket", "subject": "help"}], "count": 1}

        def list_comments(self, *, ticket_id):
            return {
                "comments": [
                    {
                        "id": 1,
                        "public": True,
                        "body": "hi",
                        "html_body": "<b>hi</b>",
                        "via": {"channel": "web"},
                    }
                ]
            }

    monkeypatch.setattr(srv, "_client", lambda: _Client())

    def _assert_wrapped_or_machine_set(node: object, *, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}"
                if isinstance(value, str):
                    if _untrusted._is_machine_set(key):
                        continue
                    assert _untrusted.MARKER_OPEN in value, f"{child} is unwrapped: {value!r}"
                else:
                    _assert_wrapped_or_machine_set(value, path=child)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                _assert_wrapped_or_machine_set(item, path=f"{path}[{index}]")

    for name, args in [
        ("get_ticket", {"ticket_id": 1}),
        ("search_tickets", {"query": "x"}),
        ("list_comments", {"ticket_id": 1}),
    ]:
        out = srv.call_tool_sync(name, args)
        _assert_wrapped_or_machine_set(json.loads(out), path=name)


def test_get_ticket_refuses_every_ticket_when_the_read_allowlist_is_unset(monkeypatch):
    # Critical 2 (final whole-branch review): following the README's own
    # `claude mcp add`/`claude_desktop_config.json` stanzas *before this fix
    # wave* set only CSA_ZENDESK_SUBDOMAIN and CSA_ZENDESK_MCP_SERVER_IDENTIFIER
    # - never CSA_ZD_ALLOWLIST_READ - and "unset" never means "unrestricted"
    # (_scope.py): it means nothing is permitted. An operator following that
    # README exactly got a PolicyError on every get_ticket call, phrased as a
    # deliberate policy decision, which is a worse failure to diagnose than an
    # obvious misconfiguration - and NOTHING in the suite caught it, because
    # tests/conftest.py's autouse fixture sets both allowlists to "*" for
    # every other test here.
    #
    # This test opts OUT of that fixture (monkeypatch.delenv, which - like
    # every other monkeypatch call in a test - takes precedence over the
    # fixture's setenv for the life of this test only) and drives a REAL
    # PolicyBackend/FakeBackend stack through server.call_tool_sync, rather
    # than the bare-dict fake client every other test in this file
    # substitutes: the point is to prove the refusal is reachable from an MCP
    # tool call with a default install's environment, not merely from
    # policy.assert_subject_permitted() called directly (test_policy.py
    # already proves that in isolation).
    from csa_zendesk import exceptions as exc
    from csa_zendesk import policy
    from csa_zendesk.backend import FakeBackend
    from csa_zendesk.client import ZendeskClient

    monkeypatch.delenv("CSA_ZD_ALLOWLIST_READ", raising=False)
    monkeypatch.delenv("CSA_ZD_ALLOWLIST_WRITE", raising=False)

    real_client = ZendeskClient(
        policy.PolicyBackend(FakeBackend(tickets={44821: {"id": 44821}}), policy.Policy(srv.E1_CAPABILITIES))
    )
    monkeypatch.setattr(srv, "_client", lambda: real_client)

    with pytest.raises(exc.PolicyError, match="CSA_ZD_ALLOWLIST_READ"):
        srv.call_tool_sync("get_ticket", {"ticket_id": 44821})


def test_an_unknown_tool_name_is_an_error_not_a_crash():
    with pytest.raises(ValueError, match="unknown tool"):
        srv.call_tool_sync("delete_everything", {})


def test_nothing_in_the_server_module_writes_to_stdout(capsys):
    # stdout IS the JSON-RPC channel. This asserts at import and registration time.
    srv.build_server()
    assert capsys.readouterr().out == ""


# --- coverage for behaviour the brief's five tests above do not reach ------


def test_client_connects_with_the_e2_capabilities(monkeypatch):
    # The one call every other test in this file replaces via
    # `monkeypatch.setattr(srv, "_client", ...)`, so it is exercised on its
    # own here instead: `_client()` must ask `connect()` for
    # `E2_CAPABILITIES` explicitly, not `E1_CAPABILITIES` and not a named
    # profile - this task moves the server from rung E1 to rung E2 (module
    # docstring), and `_client()` requesting E1's narrower set would leave
    # every WRITE_TOOLS call refused by policy despite being registered and
    # annotated. See `test_the_server_requests_only_read_capabilities` for
    # why E1_CAPABILITIES itself (unrelated to what _client() asks for) is
    # narrower than `policy.PROFILES["readonly"]`.
    seen = {}

    def fake_connect(*, profile=None, capabilities=None, transport=None):
        seen["profile"], seen["capabilities"] = profile, capabilities
        return "a-client"

    monkeypatch.setattr(srv, "connect", fake_connect)
    assert srv._client() == "a-client"
    assert seen == {"profile": None, "capabilities": srv.E2_CAPABILITIES}


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


def test_tools_is_read_tools_plus_write_tools_plus_auth_tools():
    assert srv.TOOLS == srv.READ_TOOLS + srv.WRITE_TOOLS + srv.AUTH_TOOLS


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
    # `call_tool_sync("logout", ...)` RAISES on a failed revoke - it does not
    # catch and return a string - precisely so the caller (`_on_call_tool`)
    # can set `is_error=True` at the MCP protocol level, not just say "failed"
    # in prose. See `test_on_call_tool_reports_a_failed_logout_as_an_error_
    # result_not_a_success`, below, for the end-to-end check of that flag;
    # this test covers the message text `call_tool_sync` itself produces.
    #
    # Raises through `srv.auth.RevokeError`, not a fresh `from csa_zendesk.auth
    # import _flow` (the brief's own snippet does the latter): test_public_api.py's
    # import-time guard deletes and reimports every csa_zendesk module, including
    # `auth` and `auth._flow`, to observe a cold start. When this file's tests run
    # after that guard in the full suite, a fresh import fetches the NEW module
    # object's `RevokeError`, while `_cmd_logout`'s own `except auth.RevokeError`
    # still closes over whichever module object was current when `server`
    # was first imported - two distinct classes named `RevokeError` that `except`
    # correctly treats as unrelated, so the raised exception goes uncaught and the
    # test fails for a reason that has nothing to do with the behaviour under
    # test. Same trap, same fix, as `test_on_call_tool_wraps_a_zendesk_error_as_
    # untrusted_vendor_text`'s comment above.
    def _raise() -> str:
        raise srv.auth.RevokeError("revoke request failed")

    monkeypatch.setattr(srv.auth, "logout", _raise)
    with pytest.raises(srv.auth.RevokeError) as excinfo:
        srv.call_tool_sync("logout", {})
    message = str(excinfo.value).lower()
    assert "logged out" not in message
    assert "fail" in message or "may still be" in message


def test_a_transport_failure_during_logout_is_also_reported_as_failure(monkeypatch):
    # The other exception `auth.logout()` lets propagate - a network failure
    # reaching Zendesk's revoke endpoint, not a rejected revoke.
    def _raise() -> str:
        raise srv.exc.ApiError("could not reach the Zendesk OAuth revoke endpoint (ConnectError)")

    monkeypatch.setattr(srv.auth, "logout", _raise)
    with pytest.raises(srv.exc.ApiError) as excinfo:
        srv.call_tool_sync("logout", {})
    message = str(excinfo.value).lower()
    assert "logged out" not in message
    assert "fail" in message or "may still be" in message


def test_on_call_tool_reports_a_failed_logout_as_an_error_result_not_a_success(monkeypatch):
    # Finding 1 (Task 6 review): a failed logout must set `is_error=True`,
    # not just say "failed" in text - a host that keys retry/UI behaviour off
    # the protocol flag would otherwise treat a live, un-revoked credential as
    # a successful logout. `RevokeError` never carries Zendesk response-body
    # text (`_NEVER_WRAP`, at `_on_call_tool`), so it comes back unwrapped.
    from mcp import types as mcp_types

    from csa_zendesk import _untrusted

    def _raise() -> str:
        raise srv.auth.RevokeError("revoke request failed")

    monkeypatch.setattr(srv.auth, "logout", _raise)
    params = mcp_types.CallToolRequestParams(name="logout", arguments={})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    text = result.content[0].text
    assert "logged out" not in text.lower()
    assert _untrusted.MARKER_OPEN not in text


def test_on_call_tool_reports_a_logout_transport_failure_as_a_wrapped_error(monkeypatch):
    # `exc.ApiError` is a MIXED type (Finding 2) - some instances carry
    # Zendesk response-body text, some (like this one) are this library's own
    # connectivity diagnostic. A class-level check cannot tell them apart, so
    # it defaults to the safe side and is wrapped here, even though this
    # particular instance is "ours" - over-wrapping an all-ours message is
    # noise, not the vulnerability `_untrusted` exists to close.
    from mcp import types as mcp_types

    from csa_zendesk import _untrusted

    def _raise() -> str:
        raise srv.exc.ApiError("could not reach the Zendesk OAuth revoke endpoint (ConnectError)")

    monkeypatch.setattr(srv.auth, "logout", _raise)
    params = mcp_types.CallToolRequestParams(name="logout", arguments={})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    text = result.content[0].text
    assert _untrusted.MARKER_OPEN in text


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


def test_authenticate_raises_when_the_post_login_identity_check_fails(monkeypatch):
    # Important 6 (final whole-branch review): this used to be a plain
    # returned string, which `call_tool_sync` handed back normally and
    # `_on_call_tool` then reported with `is_error=False` - a token that
    # authenticates nothing, read as success. Now it RAISES
    # `auth.NotAuthenticated`, the same way `_cmd_logout` raises on a failed
    # revoke, so `call_tool_sync` itself no longer returns on this path. See
    # `test_on_call_tool_reports_a_failed_post_login_identity_check_as_an_
    # error_result`, below, for the end-to-end check of the `is_error` flag
    # this enables - this test covers the message text `_cmd_authenticate`
    # itself produces.
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
    with pytest.raises(srv.auth.NotAuthenticated) as excinfo:
        srv.call_tool_sync("authenticate", {})
    message = str(excinfo.value)
    assert "identity check" in message.lower()
    assert "AT-X" not in message and "RT-X" not in message


def test_on_call_tool_reports_a_failed_post_login_identity_check_as_an_error_result(monkeypatch):
    # The end-to-end check the finding actually demands: tested THROUGH
    # `_on_call_tool`, not `call_tool_sync` directly, because `is_error` is a
    # property of the `CallToolResult` only `_on_call_tool` builds - a test
    # that only inspects the message text (the one above) never observes
    # whether the protocol-level flag agrees with it.
    from mcp import types as mcp_types

    from csa_zendesk import _untrusted
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
    params = mcp_types.CallToolRequestParams(name="authenticate", arguments={})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    text = result.content[0].text
    assert "identity check" in text.lower()
    assert "AT-X" not in text and "RT-X" not in text
    # auth.NotAuthenticated's message is this library's own diagnostic, never
    # Zendesk response-body text - it stays unwrapped, per _NEVER_WRAP.
    assert _untrusted.MARKER_OPEN not in text


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


def test_a_missing_subdomain_during_authenticate_is_reported_unwrapped(monkeypatch):
    # Finding 2 (Task 6 review): `auth.NotAuthorised` (a `ZendeskError`
    # subclass) carries this library's own configuration prose, never text
    # from a Zendesk response body - wrapping it as untrusted would tell the
    # model to distrust its own setup instructions. `NotAuthorised` is in
    # `_NEVER_WRAP` for exactly this reason.
    from mcp import types as mcp_types

    from csa_zendesk import _untrusted

    def _raise(**_kwargs: object) -> None:
        raise srv.auth.NotAuthorised(
            "CSA_ZENDESK_SUBDOMAIN is not set. Set it to the Zendesk subdomain this server talks to."
        )

    monkeypatch.setattr(srv.auth, "login", _raise)
    params = mcp_types.CallToolRequestParams(name="authenticate", arguments={})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    text = result.content[0].text
    assert _untrusted.MARKER_OPEN not in text
    assert "CSA_ZENDESK_SUBDOMAIN" in text


def test_never_wrap_covers_every_exception_type_the_module_docstring_enumerates():
    # A change to `_NEVER_WRAP` should be a deliberate edit to the enumerated
    # comment above it, not an accidental drop - this pins the exact set.
    #
    # Every member is read off `srv.auth`/`srv.exc` - the exact module objects
    # `server.py` itself closes over - rather than a fresh `from csa_zendesk...
    # import ...`, for the same module-identity reason documented on
    # `test_a_genuine_revoke_failure_is_reported_as_failure_not_as_logged_out`
    # above: a fresh import after test_public_api.py's reload guard has run
    # would fetch a different (if equal-looking) class object, and this set
    # comparison would fail for a reason unrelated to `_NEVER_WRAP` itself.
    assert set(srv._NEVER_WRAP) == {
        srv.exc.PolicyError,
        srv.exc.InvalidPath,
        srv.exc.SearchLimitExceeded,
        srv.exc.EmptyWrite,
        srv.exc.InvalidFilename,
        srv.exc.RateLimited,
        srv.exc.ServiceUnavailable,
        srv.auth.NotAuthorised,
        srv.auth.TokenAlreadyInvalid,
        srv.auth.TokenFileError,
        srv.auth.AuthExchangeError,
        srv.auth.NotAuthenticated,
        srv.auth.RevokeError,
    }


def test_a_mixed_type_instance_that_is_entirely_our_own_prose_still_wraps(monkeypatch):
    # Documents the accepted trade-off for a MIXED type (Finding 2): this
    # `exc.ApiError` instance carries no Zendesk response-body text at all
    # (it is `_http.py`'s own connectivity diagnostic, reached here through
    # `get_ticket`), yet it is wrapped anyway because `exc.ApiError` cannot be
    # told apart from a vendor-derived instance at the class level.
    from mcp import types as mcp_types

    from csa_zendesk import _untrusted

    class _Client:
        def get_ticket(self, *, ticket_id: int) -> dict[str, object]:
            raise srv.exc.ApiError("could not reach Zendesk (ConnectError) requesting GET /api/v2/tickets/1")

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    params = mcp_types.CallToolRequestParams(name="get_ticket", arguments={"ticket_id": 1})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    assert _untrusted.MARKER_OPEN in result.content[0].text


def test_credentials_rejected_wraps_the_vendor_text_but_not_the_remedy(monkeypatch):
    # Important 7 (final whole-branch review): `_errors.parse_error`'s 401
    # branch splices Zendesk's own error text into `message` but keeps this
    # library's remedy sentence separate on `CredentialsRejected.remedy` -
    # this is the one message an operator most needs to read as authoritative,
    # so it must not reach the model inside the same untrusted-content markers
    # that tell it to discount vendor text.
    from mcp import types as mcp_types

    from csa_zendesk import _untrusted

    class _Client:
        def get_ticket(self, *, ticket_id: int) -> dict[str, object]:
            raise srv.exc.CredentialsRejected(
                "Zendesk rejected the credential (Couldn't authenticate you).",
                remedy="Call the `authenticate` tool to log in again.",
            )

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    params = mcp_types.CallToolRequestParams(name="get_ticket", arguments={"ticket_id": 1})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    text = result.content[0].text
    # The vendor-derived half is wrapped...
    assert _untrusted.MARKER_OPEN in text
    assert "Couldn't authenticate you" in text
    # ...but the remedy sentence is not - it must be readable as ours, not
    # discountable as untrusted content.
    assert "Call the `authenticate` tool to log in again." in text
    remedy_start = text.index("Call the `authenticate` tool")
    assert _untrusted.MARKER_OPEN not in text[remedy_start:]
    assert _untrusted.MARKER_CLOSE not in text[remedy_start:]


def test_credentials_rejected_with_no_remedy_wraps_the_whole_message(monkeypatch):
    # The other raise site (`_http.py`'s empty-access-token check) composes no
    # vendor text at all and sets no `remedy` - `CredentialsRejected.remedy`
    # defaults to `None`, and the whole (all-ours) message is wrapped, the
    # same accepted over-wrapping trade-off `exc.ApiError` gets.
    from mcp import types as mcp_types

    from csa_zendesk import _untrusted

    class _Client:
        def get_ticket(self, *, ticket_id: int) -> dict[str, object]:
            raise srv.exc.CredentialsRejected("the token provider returned an empty access token. Re-authorise.")

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    params = mcp_types.CallToolRequestParams(name="get_ticket", arguments={"ticket_id": 1})
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    text = result.content[0].text
    assert text.startswith(_untrusted.MARKER_OPEN)
    assert text.endswith(_untrusted.MARKER_CLOSE)


def test_authenticate_description_says_it_can_take_a_while():
    (t,) = [t for t in srv.AUTH_TOOLS if t.name == "authenticate"]
    assert "5 minutes" in t.description or "300" in t.description or "minutes" in t.description.lower()


def test_the_server_instructions_tell_the_model_not_to_retry_or_hunt_for_files():
    text = srv.INSTRUCTIONS.lower()
    assert "authenticate" in text
    assert "do not retry" in text
    assert "credential file" in text


def test_build_server_carries_the_instructions():
    server = srv.build_server()
    assert server.instructions == srv.INSTRUCTIONS


def test_build_server_reports_the_package_version_not_a_restated_literal():
    # server.py imports __version__ rather than hardcoding a string precisely so
    # the two cannot diverge - a stale literal here is worse than a stale doc,
    # because it is what every connecting client is told, machine-readable, and
    # believed. Importing csa_zendesk here (not csa_zendesk.server's own import)
    # is what makes this test fail if the two ever come from different places.
    import csa_zendesk

    server = srv.build_server()
    assert server.version == csa_zendesk.__version__


# --- Task 7: rung E1 - the capability profile, and the refusal it backs ----


def test_the_server_requests_only_read_capabilities():
    caps = srv.E1_CAPABILITIES
    assert all(c.endswith(".read") or c == "ticket.read" for c in caps), caps
    assert not any("write" in c or "reply" in c or "close" in c or "solve" in c for c in caps)


def test_every_registered_tool_gates_on_the_capability_its_annotation_implies():
    # Replaces test_no_registered_tool_maps_to_a_write_operation (which
    # asserted every non-auth tool gates on TICKET_READ - true only while
    # this server had no write tools at all). Task 5's amendment calls for a
    # replacement "that still has teeth - assert the exact name -> capability
    # mapping for every registered tool, so a tool that silently changes gate
    # fails the build." This does that: every entry is spelled out, so a
    # tool's gate changing (or a new tool arriving with no entry here) fails
    # loudly rather than the test going vacuous.
    from csa_zendesk import policy

    auth_names = {t.name for t in srv.AUTH_TOOLS}
    expected_gate = {
        "get_ticket": policy.TICKET_READ,
        "search_tickets": policy.TICKET_READ,
        "list_comments": policy.TICKET_READ,
        "get_attachment": policy.TICKET_READ,
        "update_ticket": policy.TICKET_WRITE,
        "assign_ticket": policy.TICKET_WRITE,
        "add_internal_note": policy.TICKET_NOTE,
        "solve_ticket": policy.TICKET_SOLVE,
        "upload_file": policy.TICKET_ATTACH,
        "delete_upload": policy.TICKET_ATTACH,
    }
    # Every non-auth tool this server registers is accounted for above - a
    # tool added to TOOLS with no matching entry here would otherwise be
    # silently skipped by the loop below, the exact failure shape this
    # task's brief calls out repeatedly.
    assert {t.name for t in srv.TOOLS if t.name not in auth_names} == set(expected_gate)
    for t in srv.TOOLS:
        if t.name in auth_names:
            continue
        assert policy._GATES[t.name] == expected_gate[t.name], t.name


def test_write_tools_are_registered_and_annotated_as_writes():
    # Per the orchestrator amendment: WRITE_TOOLS is exactly the six tools
    # that write, get_attachment is NOT among them (it lives in READ_TOOLS),
    # and every member is read_only_hint=False with no special case.
    names = {t.name for t in srv.WRITE_TOOLS}
    assert names == {
        "update_ticket",
        "assign_ticket",
        "add_internal_note",
        "solve_ticket",
        "upload_file",
        "delete_upload",
    }
    for t in srv.WRITE_TOOLS:
        assert t.annotations.read_only_hint is False, t.name


def test_only_delete_upload_is_destructive():
    destructive = {t.name for t in srv.WRITE_TOOLS if t.annotations.destructive_hint}
    assert destructive == {"delete_upload"}


def test_solve_ticket_is_annotated_non_destructive_and_idempotent():
    (t,) = [t for t in srv.WRITE_TOOLS if t.name == "solve_ticket"]
    assert t.annotations.destructive_hint is False
    assert t.annotations.idempotent_hint is True


def test_reply_publicly_and_merge_and_close_are_not_registered():
    # Reach (E5) and irreversibility are separate rungs from E2. A tool a
    # model can see but must not use is worse than an absent one (task brief).
    names = {t.name for t in srv.TOOLS}
    assert "reply_publicly" not in names
    assert "merge_tickets" not in names
    assert "close_ticket" not in names


def test_e2_capabilities_is_e1_plus_the_four_write_capabilities():
    from csa_zendesk import policy

    assert srv.E2_CAPABILITIES == srv.E1_CAPABILITIES | {
        policy.TICKET_WRITE,
        policy.TICKET_NOTE,
        policy.TICKET_SOLVE,
        policy.TICKET_ATTACH,
    }
    # E1's own grant is not lost moving to E2 - get_attachment (gated on
    # TICKET_READ) keeps working at E2 exactly as it does at E1.
    assert policy.TICKET_READ in srv.E2_CAPABILITIES


def test_no_tool_path_returns_an_unwrapped_envelope(monkeypatch):
    # The block's security property, asserted over every registered data
    # tool rather than the ones we happened to think of - and actually
    # enforced as such: the assertion just below fails the build the moment
    # `args` and `READ_TOOLS | WRITE_TOOLS` diverge, rather than the loop
    # silently skipping a data tool that has no matching `args` entry. Auth
    # tools are excluded by name (`AUTH_TOOLS`), not by omission - the same
    # discipline `test_every_registered_tool_gates_on_the_capability_its_
    # annotation_implies` uses - so the exclusion is stated rather than
    # accidental. Extended (this task) to cover the six WRITE_TOOLS and
    # get_attachment: a write tool returns the updated ticket, which carries
    # the requester's own text just as much as a read does, and that is
    # exactly the property this test exists to hold onto as the tool
    # surface grows.
    from csa_zendesk import _untrusted

    class _Client:
        def get_ticket(self, *, ticket_id):
            return {"ticket": {"id": 1, "subject": "s"}}

        def search_tickets(self, *, query, page=1, per_page=25):
            return {"results": [{"id": 1, "subject": "s"}], "count": 1}

        def list_comments(self, *, ticket_id):
            return {"comments": [{"id": 1, "body": "b", "public": True}]}

        def get_attachment(self, *, attachment_id):
            return {"attachment": {"id": attachment_id, "file_name": "evidence.log"}}

        def update_ticket(self, *, ticket_id, fields):
            return {"ticket": {"id": ticket_id, "subject": "s"}}

        def assign_ticket(self, *, ticket_id, assignee_id=None, group_id=None):
            return {"ticket": {"id": ticket_id, "subject": "s"}}

        def add_internal_note(self, *, ticket_id, body, uploads=None):
            return {"ticket": {"id": ticket_id, "subject": "s"}}

        def solve_ticket(self, *, ticket_id):
            return {"ticket": {"id": ticket_id, "subject": "s"}}

        def upload_file(self, *, filename, content, content_type):
            return {"upload": {"token": "tok", "attachment": {"file_name": filename}}}

        def delete_upload(self, *, token):
            return {"deleted": {"token": token}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    args = {
        "get_ticket": {"ticket_id": 1},
        "search_tickets": {"query": "x"},
        "list_comments": {"ticket_id": 1},
        "get_attachment": {"attachment_id": 1},
        "update_ticket": {"ticket_id": 1, "fields": {"subject": "new"}},
        "assign_ticket": {"ticket_id": 1, "assignee_id": 2},
        "add_internal_note": {"ticket_id": 1, "body": "note"},
        "solve_ticket": {"ticket_id": 1},
        "upload_file": {"filename": "a.pdf", "content_base64": "eA==", "content_type": "application/pdf"},
        "delete_upload": {"token": "tok"},
    }
    data_tool_names = {t.name for t in srv.READ_TOOLS} | {t.name for t in srv.WRITE_TOOLS}
    assert data_tool_names == set(args), "a data tool was added without a matching `args` entry"
    auth_names = {t.name for t in srv.AUTH_TOOLS}
    for t in srv.TOOLS:
        if t.name in auth_names:
            continue
        assert _untrusted.MARKER_OPEN in srv.call_tool_sync(t.name, args[t.name]), t.name


# --- Task 5 (SDD): the write tools, rung E2 ---------------------------------


def test_get_attachment_forwards_attachment_id(monkeypatch):
    seen = {}

    class _Client:
        def get_attachment(self, *, attachment_id):
            seen["attachment_id"] = attachment_id
            return {"attachment": {"id": attachment_id, "file_name": "log.txt"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    out = srv.call_tool_sync("get_attachment", {"attachment_id": 42})
    assert seen == {"attachment_id": 42}
    assert "42" in out


def test_update_ticket_forwards_ticket_id_and_fields(monkeypatch):
    seen = {}

    class _Client:
        def update_ticket(self, *, ticket_id, fields):
            seen["ticket_id"], seen["fields"] = ticket_id, fields
            return {"ticket": {"id": ticket_id, "subject": "s"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    out = srv.call_tool_sync("update_ticket", {"ticket_id": 7, "fields": {"priority": "high"}})
    assert seen == {"ticket_id": 7, "fields": {"priority": "high"}}
    assert out


def test_assign_ticket_forwards_optional_assignee_and_group(monkeypatch):
    seen = {}

    class _Client:
        def assign_ticket(self, *, ticket_id, assignee_id=None, group_id=None):
            seen["ticket_id"], seen["assignee_id"], seen["group_id"] = ticket_id, assignee_id, group_id
            return {"ticket": {"id": ticket_id, "subject": "s"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    srv.call_tool_sync("assign_ticket", {"ticket_id": 7, "group_id": 99})
    assert seen == {"ticket_id": 7, "assignee_id": None, "group_id": 99}


def test_add_internal_note_forwards_body_and_uploads(monkeypatch):
    seen = {}

    class _Client:
        def add_internal_note(self, *, ticket_id, body, uploads=None):
            seen["ticket_id"], seen["body"], seen["uploads"] = ticket_id, body, uploads
            return {"ticket": {"id": ticket_id, "subject": "s"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    srv.call_tool_sync("add_internal_note", {"ticket_id": 7, "body": "internal note", "uploads": ["tok1"]})
    assert seen == {"ticket_id": 7, "body": "internal note", "uploads": ["tok1"]}


def test_solve_ticket_forwards_ticket_id(monkeypatch):
    seen = {}

    class _Client:
        def solve_ticket(self, *, ticket_id):
            seen["ticket_id"] = ticket_id
            return {"ticket": {"id": ticket_id, "status": "solved", "subject": "s"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    out = srv.call_tool_sync("solve_ticket", {"ticket_id": 7})
    assert seen == {"ticket_id": 7}
    assert "solved" in out


def test_upload_file_decodes_base64_content_before_calling_the_client(monkeypatch):
    seen = {}

    class _Client:
        def upload_file(self, *, filename, content, content_type):
            seen["filename"], seen["content"], seen["content_type"] = filename, content, content_type
            return {"upload": {"token": "tok-1"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    import base64

    encoded = base64.b64encode(b"hello world").decode()
    out = srv.call_tool_sync(
        "upload_file", {"filename": "report.pdf", "content_base64": encoded, "content_type": "application/pdf"}
    )
    assert seen == {"filename": "report.pdf", "content": b"hello world", "content_type": "application/pdf"}
    assert out


def test_upload_file_with_malformed_base64_is_an_error_not_a_crash(monkeypatch):
    # binascii.Error is a ValueError subclass, so `_on_call_tool`'s existing
    # `except ValueError` branch (written for an unknown tool name) already
    # covers a malformed content_base64 with no new exception branch needed -
    # this is the end-to-end check that it actually does.
    from mcp import types as mcp_types

    from csa_zendesk import _untrusted

    class _Client:
        def upload_file(self, *, filename, content, content_type):
            raise AssertionError("should never be reached with malformed base64")

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    params = mcp_types.CallToolRequestParams(
        name="upload_file",
        arguments={"filename": "a.pdf", "content_base64": "not-valid-base64!!!", "content_type": "application/pdf"},
    )
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    text = result.content[0].text
    # Ours (a stdlib diagnostic about the argument this process was handed),
    # not wrapped - the same provenance rule as an unknown tool name.
    assert _untrusted.MARKER_OPEN not in text


def test_delete_upload_forwards_token(monkeypatch):
    seen = {}

    class _Client:
        def delete_upload(self, *, token):
            seen["token"] = token
            return {"deleted": {"token": token}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    srv.call_tool_sync("delete_upload", {"token": "tok-abc"})
    assert seen == {"token": "tok-abc"}


def test_a_write_tool_response_reaches_on_call_tool_fully_wrapped(monkeypatch):
    from mcp import types as mcp_types

    from csa_zendesk import _untrusted

    class _Client:
        def update_ticket(self, *, ticket_id, fields):
            return {"ticket": {"id": ticket_id, "subject": "requester wrote this"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    params = mcp_types.CallToolRequestParams(
        name="update_ticket", arguments={"ticket_id": 1, "fields": {"priority": "high"}}
    )
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is False
    assert _untrusted.MARKER_OPEN in result.content[0].text


def test_upload_file_refuses_base64_that_would_silently_decode_to_nothing(monkeypatch):
    # `base64.b64decode` DISCARDS non-alphabet characters before checking
    # padding, so b64decode("!!!!") == b"" with no error. Without
    # validate=True this argument would decode to an empty file, upload
    # successfully, and return a token for a zero-byte attachment - silent
    # corruption, not a refusal. The call must never reach the client.
    from mcp import types as mcp_types

    class _Client:
        def upload_file(self, *, filename, content, content_type):  # pragma: no cover - must never run
            raise AssertionError("upload_file was reached with silently-emptied content")

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    params = mcp_types.CallToolRequestParams(
        name="upload_file",
        arguments={"filename": "r.pdf", "content_base64": "!!!!", "content_type": "application/pdf"},
    )
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is True
    assert "base64" in result.content[0].text.lower()


def test_upload_file_accepts_well_formed_base64(monkeypatch):
    # The other half of the pair: validate=True must not reject valid input.
    from mcp import types as mcp_types

    seen = {}

    class _Client:
        def upload_file(self, *, filename, content, content_type):
            seen["content"] = content
            return {"upload": {"token": "t"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    params = mcp_types.CallToolRequestParams(
        name="upload_file",
        arguments={"filename": "r.pdf", "content_base64": "SGVsbG8=", "content_type": "application/pdf"},
    )
    result = asyncio.run(srv._on_call_tool(None, params))
    assert result.is_error is False
    assert seen["content"] == b"Hello"


def test_the_attachment_family_is_labelled_with_its_own_provenance_not_a_ticket(monkeypatch):
    # The `_untrusted` tests pin what each wrapper LABELS; this pins which one
    # `call_tool_sync` reaches for, which is where the defect actually was -
    # all three of these were put through `wrap_ticket` and came back claiming
    # `source=zendesk-ticket...` for data that never came from a ticket. No
    # existing test failed when that was fixed, so this is the one that would
    # have caught it.
    class _Client:
        def upload_file(self, *, filename, content, content_type):
            return {"upload": {"token": "t", "attachment": {"file_name": "r.pdf"}}}

        def delete_upload(self, *, token):
            return {"upload": {"file_name": "r.pdf"}}

        def get_attachment(self, *, attachment_id):
            return {"attachment": {"id": 1, "file_name": "r.pdf"}}

    monkeypatch.setattr(srv, "_client", lambda: _Client())
    expected = {
        "upload_file": (
            "zendesk-upload",
            {"filename": "r.pdf", "content_base64": "SGk=", "content_type": "text/plain"},
        ),
        "delete_upload": ("zendesk-upload", {"token": "t"}),
        "get_attachment": ("zendesk-attachment", {"attachment_id": 1}),
    }
    registered = {t.name for t in srv.READ_TOOLS + srv.WRITE_TOOLS}
    assert set(expected) <= registered, "a tool was renamed without updating this test"
    for name, (root, args) in expected.items():
        text = srv.call_tool_sync(name, args)
        assert f"source={root}" in text, name
        assert "zendesk-ticket" not in text, name
