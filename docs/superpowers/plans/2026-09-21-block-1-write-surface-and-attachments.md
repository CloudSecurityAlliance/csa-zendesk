# Block 1 — The Write Surface and Attachments

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take `csa-zendesk` from rung E1 (read the queue, change nothing) to rung **E2** — work tickets for real: update fields, assign, add internal notes, solve, and attach files — against an allowlisted set of test tickets.

**Architecture:** Two pieces, sequenced. First the **E8 split** the project already owes: `_http.py` is 407 lines against ADR-002's ~400 tripwire, and TODO E8 says the split must land *before* the next addition. Then the write surface itself: new `Backend` methods, their `policy._GATES` entries, their `tools.ToolSpec` constraints, and the MCP tools. Attachments are the reason the split cannot be deferred again — every call in this library today is `json=`; an upload is a **binary body** with a required `filename` query parameter, which is a second kind of request the transport has never made.

**Tech Stack:** Python ≥3.10 · `httpx` · `mcp>=2.2,<3` · `pytest` · `ruff` · `mypy --strict`

**Spec:** [`docs/superpowers/specs/2026-09-17-csa-zendesk-whole-project-design.md`](../specs/2026-09-17-csa-zendesk-whole-project-design.md) — §1 the three controls, §5's enablement ladder (this block delivers **E2**). Tool boundaries are [`ADR-016`](../../../DECISIONS-ADR/ADR-016.md) (a tool is an operation **plus the constraint that fixes its impact**). Capabilities are [`ADR-010`](../../../DECISIONS-ADR/ADR-010.md); the backend seam is [`ADR-002`](../../../DECISIONS-ADR/ADR-002.md). Vendor behaviour is [`analysis/API-SURFACE.md`](../../../analysis/API-SURFACE.md). Default posture is [DEC-017](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/DECISIONS.md) — within green, get work done by default; client-side restriction is for fit, not security.

## The fact this whole block turns on

`analysis/API-SURFACE.md` §5.4f: **`comment.public` has no fixed default — it inherits from the ticket's first comment.** Confirmed live on 2026-09-21 against a real email-originated ticket on the CSA tenant: `via: email`, comment public-flags `[True, False]` — one public, one private, on a single ticket.

An email-originated ticket's first comment is public. So on the fixture this block will be tested against, **every comment defaults to public unless something forces otherwise** — and a note that reaches the customer is the worst failure available here. `add_internal_note` forcing `public=false` is not belt-and-braces; it is the single control that makes the tool safe, and the constraint belongs in `tools.ToolSpec`, at the dispatch seam, not in a tool description.

## What is deliberately NOT in this block

- **`reply_publicly`** and **`merge_tickets`** — the two that reach a customer. Reach is a separate rung (E5) and a separate switch.
- **`close_ticket`** — `API-SURFACE.md` §5.4d records that Zendesk's `closed` is **terminal**. Irreversible in the strongest sense; it wants its own decision, not a line in a write block.
- **`triggers:write`** — present in the OAuth client's ceiling (screenshot, 2026-09-21) but admin configuration, which is rung E3/E4.
- **Rate-limit accounting and pagination-following** — the other two additions E8 names. The split makes them cheap; building them is not this block.

## Global Constraints

- **Nothing in the package may write to stdout.** Under stdio MCP, stdout **is** the JSON-RPC channel. `cli.py` is the one sanctioned exception, justified in its module docstring.
- **Never interpolate a credential** into a message, a log line, an exception, or a `__repr__`.
- **100% test coverage, enforced** by `--cov-fail-under=100`. `# pragma: no cover` needs a justifying comment on the line. When a coverage gate demands a branch be exercised, ask whether the branch should exist rather than inventing behaviour to fill it.
- **`mypy --strict` over `src`.** `ruff check src tests` **and** `ruff format --check src tests` — CI runs both, and a branch has been merged red on `ruff format` before.
- **No network in tests, ever.** `httpx.MockTransport` only.
- **Keyword-only arguments** on every `Backend` method, so `PolicyBackend` can wrap uniformly.
- **Every `Backend` method returns the RAW upstream envelope.** Shaping belongs to the delivery layer (ADR-002).
- **Adding a `Backend` method obliges four things now**: an `ApiBackend` implementation, a `FakeBackend` implementation, a `policy._GATES` entry, and — since Block 0e — a `tools.TOOLS` entry. `tests/test_tools.py::test_every_gated_backend_method_has_a_tool_spec` enforces the fourth; a method with a gate and no `ToolSpec` is ungoverned by the allowlist, which is how `list_comments` shipped unscoped.
- **`python3 scripts/check_public_safe.py` must pass before every commit.** The repo is public; never write the real subdomain into a tracked file.
- **`tests/test_public_api.py` hardcodes a module count** checked against a recursive walk. Adding a module changes it. Update the number; **do not delete the assertion.**

## File Structure

| File | Responsibility |
|---|---|
| `src/csa_zendesk/_transport.py` | **Create (Task 1).** Send, retry, the retry budget, error translation, the auth hook, the invalid-token retry. Knows nothing about Zendesk's paths or envelopes. |
| `src/csa_zendesk/_http.py` | **Modify (Task 1).** Keeps `HttpClient`: path validation, envelope checking, `get`/`request`/`post_binary`. Delegates sending to `_transport`. |
| `src/csa_zendesk/backend.py` | **Modify (Tasks 2–4).** Six new methods on `Backend`, `ApiBackend`, `FakeBackend`. |
| `src/csa_zendesk/policy.py` | **Modify (Tasks 2–4).** New capability `TICKET_ATTACH`; six new `_GATES` entries. |
| `src/csa_zendesk/tools.py` | **Modify (Tasks 2–4).** Six new `ToolSpec` entries with their constraints. |
| `src/csa_zendesk/client.py` | **Modify (Tasks 2–4).** Six pass-throughs. |
| `src/csa_zendesk/server.py` | **Modify (Task 5).** `WRITE_TOOLS`; `TOOLS = READ_TOOLS + WRITE_TOOLS + AUTH_TOOLS`. |
| `analysis/tool-boundaries.csv`, `analysis/API-SURFACE.md`, `TODO.md`, `README.md` | **Modify (Tasks 1, 5, 6).** |

---

### Task 1: The E8 split — a transport that can send more than JSON

**Files:**
- Create: `src/csa_zendesk/_transport.py`, `tests/test_transport.py`
- Modify: `src/csa_zendesk/_http.py`, `tests/test_http.py`, `tests/test_public_api.py`

**Interfaces:**
- Consumes: `httpx`, `csa_zendesk._errors.parse_error`, `csa_zendesk.exceptions`.
- Produces: `Transport` — holds the `httpx.Client`, the auth event hook, the retry loop, the retry budget and the `on_invalid_token` retry. Exposes `send(method, path, *, params=None, json=None, content=None, content_type=None) -> httpx.Response`. `HttpClient` keeps its existing public surface (`get`, `request`) unchanged and gains `post_binary`.

**Why this is Task 1 and not deferred again.** `TODO.md` **E8**: *"`_http.py` has crossed ADR-002's ~400-line tripwire — 407 lines... Rate-limit accounting and pagination-following are still owed, and **the split must happen before either lands**."* It also says *"Plan the split as its own piece of work rather than folding it into the next feature that needs `_http.py`."* Attachments are that next feature. The split is here as its own task with its own review gate, which is the closest this plan can come to honouring that while still delivering the block.

**This task changes no behaviour.** Every existing test must pass untouched. If a test needs editing to accommodate the split, that is a signal the split changed something — stop and say so in your report rather than editing the test.

**Where the seam goes.** `_http.py` today mixes two jobs. Read it before splitting; the current members are `MAX_RETRIES`, `MAX_RETRY_AFTER_SECONDS`, `MAX_TOTAL_RETRY_SECONDS`, `_NON_IDEMPOTENT_RETRYABLE`, `_IDEMPOTENT_RETRYABLE`, `_is_invalid_token`, and on `HttpClient`: `__init__`, `_authorize`, `__repr__`, `get`, `request`, `_validate_path`, `_send`, `_budget_exhausted`, `_body_or_none`, `_envelope`.

- **Transport** (moves): the retry constants, `_is_invalid_token`, `_authorize`, `_send`, `_budget_exhausted`, and the retry loop currently inside `request`.
- **HttpClient** (stays): `_validate_path`, `_envelope`, `_body_or_none`, `get`, `request`, `__repr__`.

The test that tells you the seam is right: **`_transport.py` should contain no string starting `/api/v2`, and no knowledge of what a Zendesk envelope looks like.**

- [ ] **Step 1: Write the characterisation test first**

Before moving anything, add a test to `tests/test_http.py` that pins the behaviour the split must preserve, so a regression is loud:

```python
def test_the_retry_budget_and_invalid_token_retry_survive_the_split():
    # Pins the two behaviours most likely to break when the retry loop moves:
    # a 429 is retried within budget, and an invalid_token 401 refreshes once.
    calls = {"n": 0, "refreshed": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate"})
        if calls["n"] == 2:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"ticket": {"id": 1}})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: "AT",
        on_invalid_token=lambda: calls.__setitem__("refreshed", calls["refreshed"] + 1),
        transport=httpx.MockTransport(handler),
    )
    assert c.get("/api/v2/tickets/1") == {"ticket": {"id": 1}}
    assert calls["n"] == 3
    assert calls["refreshed"] == 1
```

- [ ] **Step 2: Run it and watch it pass**

Run: `./.venv/bin/pytest tests/test_http.py -k survive_the_split -v`
Expected: PASS. It characterises today's behaviour; it must still pass after the split.

- [ ] **Step 3: Create `_transport.py` and move the transport members**

Move, do not rewrite. Keep every comment — several record measured vendor behaviour and the reasoning behind the retry budget. `Transport.send` takes `content` and `content_type` alongside `json`, both defaulting to `None`; passing both `json` and `content` is a `ValueError`.

- [ ] **Step 4: Reduce `HttpClient` to delegation, add `post_binary`**

```python
def post_binary(
    self, path: str, *, content: bytes, content_type: str, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """POST raw bytes. Zendesk's upload endpoint takes a binary body and a
    `filename` query parameter - it is the only call in this library that is
    not JSON, which is why `Transport.send` grew `content`/`content_type`."""
    self._validate_path(path)
    response = self._transport.send("POST", path, params=params, content=content, content_type=content_type)
    return self._envelope(response)
```

- [ ] **Step 5: Run the full suite — unchanged**

Run: `./.venv/bin/pytest --cov=csa_zendesk --cov-fail-under=100 -q`
Expected: every pre-existing test passes with no edits. Update `tests/test_public_api.py`'s module count for the new module.

- [ ] **Step 6: Update E8 and commit**

`TODO.md` E8: the split has landed; say what moved, and that rate-limit accounting and pagination-following are now cheap additions to `_transport.py`. Do not close E8 if those two remain owed — narrow it to what is left.

```bash
./.venv/bin/ruff check src tests && ./.venv/bin/ruff format --check src tests
./.venv/bin/mypy --strict src && python3 scripts/check_public_safe.py
git add -A && git commit -m "refactor: split the transport out of _http, so uploads are additive"
```

---

### Task 2: `update_ticket` and `assign_ticket` — fields only, never a comment

**Files:**
- Modify: `src/csa_zendesk/backend.py`, `policy.py`, `tools.py`, `client.py`
- Test: `tests/test_backend.py`, `tests/test_policy.py`, `tests/test_tools.py`, `tests/test_client.py`

**Interfaces:**
- Produces: `Backend.update_ticket(*, ticket_id: int, fields: dict[str, Any]) -> Envelope`; `Backend.assign_ticket(*, ticket_id: int, assignee_id: int | None = None, group_id: int | None = None) -> Envelope`; `_GATES` entries mapping both to `TICKET_WRITE`; `ToolSpec` entries with `subject_var="CSA_ZD_ALLOWLIST_WRITE"`.

**The reason these are two tools over one operation.** Both are `PUT /api/v2/tickets/{ticket_id}` — the operation that ADR-016 was written about, because it is *five impact levels in one call*: rename a field, solve, close, add an internal note, or **email a customer**, depending only on the request body. The tool is the operation **plus the constraint**. So:

- `update_ticket` carries `_forbid("comment", "status")` — it cannot comment and cannot change status.
- `assign_ticket` carries `_only("assignee_id", "group_id")` — an allowlist, which is bucket-pure in a way a denylist can never be (`analysis/SLICE-FINDINGS.md`).

Derive the path from `analysis/operation-inventory.csv` and reproduce the row in a comment, as `get_ticket` does. Do not guess the `.json` suffix in either direction.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_tools.py
def test_update_ticket_refuses_a_comment_in_the_body():
    # PUT /tickets/{id} is five impact levels in one operation (ADR-016).
    # A comment through update_ticket would reach a customer on an
    # email-originated ticket, where comment.public inherits true.
    spec = tools.TOOLS["update_ticket"]
    with pytest.raises(exc.PolicyError, match="comment"):
        spec.check({"comment": {"body": "hello"}})


def test_update_ticket_refuses_a_status_change():
    spec = tools.TOOLS["update_ticket"]
    with pytest.raises(exc.PolicyError, match="status"):
        spec.check({"status": "solved"})


def test_update_ticket_permits_an_ordinary_field():
    tools.TOOLS["update_ticket"].check({"priority": "high"})


def test_assign_ticket_permits_only_assignee_and_group():
    spec = tools.TOOLS["assign_ticket"]
    spec.check({"assignee_id": 7})
    spec.check({"group_id": 3})
    with pytest.raises(exc.PolicyError):
        spec.check({"assignee_id": 7, "priority": "high"})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `./.venv/bin/pytest tests/test_tools.py -k "update_ticket or assign_ticket" -v`
Expected: FAIL with `KeyError: 'update_ticket'`.

- [ ] **Step 3: Implement on all four layers**

`Backend` + `ApiBackend` + `FakeBackend`, then `_GATES`, then `TOOLS`, then `ZendeskClient`. Both gate to `TICKET_WRITE`. Both carry `subject_var="CSA_ZD_ALLOWLIST_WRITE"` — a write must name the ticket it may touch.

- [ ] **Step 4: Add the refusal tests**

One per tool, asserting a client without `ticket.write` is refused **before the backend is reached** — use a backend whose method raises `AssertionError` if called, as the existing read refusal tests do.

- [ ] **Step 5: Full suite, then commit**

```bash
./.venv/bin/pytest --cov=csa_zendesk --cov-fail-under=100 -q
git add -A && git commit -m "feat(backend): update_ticket and assign_ticket, each constrained to its bucket"
```

---

### Task 3: `add_internal_note` and `solve_ticket`

**Files:** as Task 2.

**Interfaces:**
- Produces: `Backend.add_internal_note(*, ticket_id: int, body: str, uploads: list[str] | None = None) -> Envelope`; `Backend.solve_ticket(*, ticket_id: int) -> Envelope`; `_GATES` mapping them to `TICKET_NOTE` and `TICKET_SOLVE`; `ToolSpec`s carrying `_force_public(False)` and `_status("solved")`.

**`_force_public(False)` is the control this block exists to get right.** Read the "fact this whole block turns on" section above before writing it. On an email-originated ticket the first comment is public and every later comment inherits that, so a note without the force **emails the customer**. The constraint lives in the `ToolSpec`, enforced at `_dispatch`, not in the tool description — a description is advice to a model, and this must hold even when the model is wrong.

`uploads` is threaded through now, so Task 4's attachment work does not have to reopen this method. It is a list of upload tokens; an empty list and `None` must behave identically.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_tools.py
def test_add_internal_note_forces_public_false():
    body = {"comment": {"body": "internal"}}
    tools.TOOLS["add_internal_note"].check(body)
    assert body["comment"]["public"] is False


def test_add_internal_note_overrides_an_attempt_to_make_it_public():
    # API-SURFACE §5.4f: on an email-originated ticket, comment.public
    # inherits true. A note that reaches the requester is the worst
    # failure this block can produce, so the force is not advisory.
    body = {"comment": {"body": "internal", "public": True}}
    tools.TOOLS["add_internal_note"].check(body)
    assert body["comment"]["public"] is False


def test_solve_ticket_sets_status_and_nothing_else():
    body: dict = {}
    tools.TOOLS["solve_ticket"].check(body)
    assert body["status"] == "solved"
```

- [ ] **Step 2: Run them and watch them fail**

- [ ] **Step 3: Implement on all four layers**

- [ ] **Step 4: Full suite, then commit**

```bash
git add -A && git commit -m "feat(backend): internal notes that cannot become public, and solve"
```

---

### Task 4: Attachments — upload, delete, attach, read

**Files:** as Task 2, plus `src/csa_zendesk/_http.py` (uses `post_binary` from Task 1).

**Interfaces:**
- Produces: `Backend.upload_file(*, filename: str, content: bytes, content_type: str) -> Envelope`; `Backend.delete_upload(*, token: str) -> Envelope`; `Backend.get_attachment(*, attachment_id: int) -> Envelope`; a new capability `TICKET_ATTACH = "ticket.attach"` in `policy.py`; `_GATES` entries; `ToolSpec`s.

**The operations, from `analysis/operation-inventory.csv` — reproduce each row in a comment:**

```
ticketing,Attachments,POST,/api/v2/uploads,UploadFiles,Upload Files,,,
ticketing,Attachments,DELETE,/api/v2/uploads/{token},DeleteUpload,Delete Upload,,,
ticketing,Attachments,GET,/api/v2/attachments/{attachment_id},ShowAttachment,Show Attachment,,,
```

**Uploading is two steps, and the gap between them is the design problem.** `POST /api/v2/uploads?filename=X` with a binary body returns an upload **token**. The file exists on Zendesk's side at that moment, attached to nothing. It becomes visible only when a later comment carries the token.

Three consequences, all of which the implementation must handle:

1. **`upload_file` reaches nobody**, so it is not a `ticket.write`. It gets its own capability, `TICKET_ATTACH`, and **no `subject_var`** — there is no ticket to scope against yet, exactly as `search_tickets` has none.
2. **An orphaned upload is invisible.** Nothing in the ticket surface will ever show a file attached to no comment. So `delete_upload` ships in the same task — without it a failed attach leaves litter nobody can find.
3. **`filename` is required and its extension must match the real file's.** The spec says so explicitly: *"While the two names can be different, their file extensions must be the same. If they don't match, the agent's browser or file reader could give an error."* Validate that the supplied `filename` has an extension; if `content_type` and the extension disagree in an obvious way, say so in the report rather than silently accepting.

**Attaching** is not a new operation: it is `add_internal_note` with `uploads=[token]`, which Task 3 already threaded through.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_backend.py
def test_upload_sends_the_filename_as_a_query_parameter_and_bytes_as_the_body():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["content"] = request.content
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(201, json={"upload": {"token": "abc123"}})

    b = ApiBackend(_client(handler))
    out = b.upload_file(filename="report.pdf", content=b"%PDF-1.7 fake", content_type="application/pdf")
    assert "filename=report.pdf" in seen["url"]
    assert seen["content"] == b"%PDF-1.7 fake"
    assert seen["content_type"] == "application/pdf"
    assert out == {"upload": {"token": "abc123"}}


def test_upload_refuses_a_filename_with_no_extension():
    # The spec requires the uploaded filename's extension to match the real
    # file's; a filename with none cannot satisfy that, and the failure would
    # surface as an unopenable attachment rather than an API error.
    def handler(request):  # pragma: no cover - must never run
        return httpx.Response(201, json={})

    with pytest.raises(exc.ZendeskError, match="extension"):
        ApiBackend(_client(handler)).upload_file(filename="report", content=b"x", content_type="application/pdf")


def test_delete_upload_targets_the_token():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(204)

    ApiBackend(_client(handler)).delete_upload(token="abc123")
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/api/v2/uploads/abc123")
```

- [ ] **Step 2: Run them and watch them fail**

- [ ] **Step 3: Add `TICKET_ATTACH` to `policy.py`**

A new capability, in `ALL_CAPABILITIES`, and added to whichever profiles should carry it. Record beside it why it is separate from `TICKET_WRITE`: an upload reaches nobody until a comment references it.

- [ ] **Step 4: Implement the three methods on all four layers**

`upload_file` uses `HttpClient.post_binary`. `get_attachment` gates to `TICKET_READ`, not `TICKET_ATTACH` — reading an attachment is a read.

- [ ] **Step 5: Full suite, then commit**

```bash
./.venv/bin/pytest --cov=csa_zendesk --cov-fail-under=100 -q
git add -A && git commit -m "feat(backend): uploads, their deletion, and reading an attachment"
```

---

### Task 5: The write tools on the MCP server

**Files:**
- Modify: `src/csa_zendesk/server.py`, `tests/test_server.py`

**Interfaces:**
- Produces: `WRITE_TOOLS`; `TOOLS = READ_TOOLS + WRITE_TOOLS + AUTH_TOOLS`; `E2_CAPABILITIES`.

**Assert on `WRITE_TOOLS` in your own tests.** Block 0e split the collections precisely so no task falsifies an earlier task's assertions — `READ_TOOLS` tests must stay true after you land. This has been broken once already by a stray `assert srv.TOOLS == srv.READ_TOOLS`; do not reintroduce that shape.

**Annotate honestly.** Every write tool is `read_only_hint=False`. `destructive_hint` is **False** for `update_ticket`, `assign_ticket`, `add_internal_note` and `upload_file` — they add or change, they do not destroy — and **True** for `delete_upload`. `solve_ticket` is `destructive_hint=False`, `idempotent_hint=True`. Remember the SDK is **mcp 2.2.0**: snake_case attributes (`read_only_hint`, `is_error`), not camelCase.

**Every response still passes through `_untrusted`.** A write returns the updated ticket, which carries the requester's text. Block 0e's `test_no_tool_path_returns_an_unwrapped_envelope` asserts `{t.name for t in READ_TOOLS} == set(args)` — extend that pattern so write tools are covered too, rather than silently skipped.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_server.py
def test_write_tools_are_registered_and_annotated_as_writes():
    names = {t.name for t in srv.WRITE_TOOLS}
    assert names == {
        "update_ticket", "assign_ticket", "add_internal_note",
        "solve_ticket", "upload_file", "delete_upload", "get_attachment",
    }
    for t in srv.WRITE_TOOLS:
        if t.name == "get_attachment":
            continue  # a read, registered here only for cohesion
        assert t.annotations.read_only_hint is False, t.name


def test_only_delete_upload_is_destructive():
    destructive = {t.name for t in srv.WRITE_TOOLS if t.annotations.destructive_hint}
    assert destructive == {"delete_upload"}


def test_reply_publicly_and_merge_and_close_are_not_registered():
    # Reach (E5) and irreversibility are separate rungs. A tool a model can
    # see but must not use is worse than an absent one.
    names = {t.name for t in srv.TOOLS}
    assert "reply_publicly" not in names
    assert "merge_tickets" not in names
    assert "close_ticket" not in names
```

- [ ] **Step 2: Run them and watch them fail**

- [ ] **Step 3: Implement `WRITE_TOOLS` and `E2_CAPABILITIES`**

`E2_CAPABILITIES = E1_CAPABILITIES | {TICKET_WRITE, TICKET_NOTE, TICKET_SOLVE, TICKET_ATTACH}`. Keep `_client()` naming its capability set explicitly; `connect()` refuses a call that names neither profile nor capabilities.

- [ ] **Step 4: Extend the unwrapped-envelope test to cover writes**

- [ ] **Step 5: Full suite, then commit**

```bash
git add -A && git commit -m "feat(server): rung E2 - work tickets, with reach still held back"
```

---

### Task 6: The record, and what the operator has to know

**Files:**
- Modify: `analysis/tool-boundaries.csv`, `analysis/API-SURFACE.md`, `README.md`, `TODO.md`, `CHANGELOG.md`

- [ ] **Step 1: `analysis/tool-boundaries.csv`**

A row per new tool. `scripts/check_boundaries.py` fails if a tool is not bucket-pure; run it.

- [ ] **Step 2: Correct `API-SURFACE.md` §7.2b — the recorded ceiling is wrong**

It records the OAuth client's ceiling as `read tickets:write ticket_attachments:write ticket_views:write`. A screenshot of the live client on 2026-09-21 shows **`triggers:write` as well**. Correct it, dated, and note that `triggers:write` is deliberately unused at this rung.

- [ ] **Step 3: `README.md` — the operator's half**

E2 needs two things E1 did not, and neither is guessable:
- the token must carry **`tickets:write ticket_attachments:write`**, so `CSA_ZENDESK_SCOPES` must be widened and `auth login` re-run — a read-scoped token fails every write with a 403 that looks like a permissions problem;
- **`CSA_ZD_ALLOWLIST_WRITE`** must name the ticket ids writes may touch, and **unset permits nothing**. State that plainly; the same omission for `CSA_ZD_ALLOWLIST_READ` was a Critical in Block 0e.

- [ ] **Step 4: `TODO.md`**

Close what this block delivered. Do **not** close F1 — the live walkthrough — until it has actually run. Add an entry for `close_ticket` recording why it was held back (terminal per §5.4d), and one for `reply_publicly`/`merge_tickets` pointing at rung E5.

- [ ] **Step 5: `CHANGELOG.md`**

An `[Unreleased]` entry. Honest about what is verified: at the time of writing, nothing in this block has run against live Zendesk.

- [ ] **Step 6: Full gates, then commit**

```bash
./.venv/bin/pytest --cov=csa_zendesk --cov-fail-under=100 -q
./.venv/bin/ruff check src tests && ./.venv/bin/ruff format --check src tests
./.venv/bin/mypy --strict src && python3 scripts/check_public_safe.py
python3 scripts/check_boundaries.py
git add -A && git commit -m "docs: what E2 needs from the operator, and the ceiling we recorded wrong"
```

---

## Self-Review

**Spec coverage.** §5's E2 rung — *"work tickets for real: + note, write; `WRITE=` test ids + self-created"* — is delivered by Tasks 2–5, with `CSA_ZD_ALLOWLIST_WRITE` as the subject control. ADR-016's rule that a tool is an operation plus its constraint is what makes `update_ticket` and `assign_ticket` two tools over one `PUT`. ADR-010's capability model gains `TICKET_ATTACH`, justified by reach rather than by verb. E8 is discharged in Task 1. **Deliberately not covered:** reach (`reply_publicly`, `merge_tickets`), `close_ticket`, admin configuration, and B1–B5's full-surface generation.

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Two steps instruct the implementer to *derive* a value rather than giving it — the API paths, from `analysis/operation-inventory.csv` — because `get_ticket`'s own comment records that guessing the `.json` suffix is a live trap here, and a wrong path is invisible to the whole suite since `FakeBackend` answers whatever it is handed.

**Type consistency.** `Envelope = dict[str, Any]` throughout. `upload_file(*, filename, content, content_type)` is identical in Tasks 4 and 5. `uploads: list[str] | None` is introduced in Task 3 and consumed in Task 4. `TICKET_ATTACH` is defined in Task 4 Step 3 and used in Task 5's `E2_CAPABILITIES`. `WRITE_TOOLS` is introduced in Task 5 and asserted on only there; `READ_TOOLS` and `AUTH_TOOLS` keep their Block 0e meanings.

**The risk I would flag to a reviewer.** Task 1 is a refactor of the library's hottest path, and its safety rests on "every existing test passes untouched". That is a weaker guarantee than it sounds: the suite is strong on `_http.py`'s error taxonomy and retry behaviour, but the invalid-token retry was only ever exercised through mocked halves until Block 0e's fix. Step 1's characterisation test exists to make that specific behaviour loud, and it is the one test in this plan I would not let an implementer weaken.
