# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Versioning policy.** Pre-1.0, `0.MINOR.PATCH` increments do not carry semver's usual
promise of a stable public surface. The tool surface (which MCP tools exist, their names
and parameters) and the library's capability names (`TICKET_READ`, and everything
`policy.ALL_CAPABILITIES` will grow into) are the parts most likely to move as the
whole-project design's B1–B5 classification and generation work lands — expect them to
change, possibly incompatibly, between `0.x` releases. `1.0.0` is the commitment that both
have stopped moving. Within `0.x`, a MINOR bump means new capability (tools, operations);
a PATCH bump means a fix with no surface change.

## [0.2.0] — 2026-09-22

**Block 1 — the write surface and attachments.** `csa-zendesk-mcp` moves from rung E1
(read-only) to rung **E2** — "work tickets for real: + note, write." Ten tools now, not
three: `get_ticket`, `search_tickets`, `list_comments`, `get_attachment` (all reads) and
`update_ticket`, `assign_ticket`, `add_internal_note`, `solve_ticket`, `upload_file`,
`delete_upload` (all writes), plus the three unchanged auth-lifecycle tools.

**Said plainly, because this is the claim most likely to be misread: nothing in this block
has run against live Zendesk.** Every write tool below was built and reviewed entirely
against `FakeBackend` and mocked transports. The one thing that *has* been verified live —
separately, from an untouched `0.1.0` clone, on 2026-09-21 — is the **read** path: OAuth,
transport, the allowlist failing closed against an unlisted id, and `_untrusted` wrapping,
all confirmed end to end against the real tenant. That is not a claim about the write
surface. The live write walkthrough is `TODO.md` F1 and happens after this branch merges,
with a human present.

### Added
- **Four new write tools** — `update_ticket`, `assign_ticket`, `add_internal_note`,
  `solve_ticket` — each a narrow, bucket-pure constraint over `PUT /api/v2/tickets/{id}`
  (ADR-016): `update_ticket` edits only an **allowlist** of nine ordinary ticket attributes
  (`subject`, `priority`, `type`, `tags`, `custom_fields`, `ticket_form_id`, `due_at`,
  `external_id`, `problem_id`) — anything else, including a field Zendesk adds later, is
  refused; `assign_ticket` can change only `assignee_id`/`group_id`; `add_internal_note`'s
  comment is structurally always private (there is no `public` parameter for a caller, or
  injected instruction, to set); `solve_ticket` can only set `status=solved`.
- **Attachments**: `upload_file` (send bytes, get back a token naming an upload attached to
  nothing yet), `delete_upload` (clean up an unattached token), and `get_attachment` (read
  one already on a ticket — gated as a read, `TICKET_READ`, not as an attach). A file
  becomes visible on a ticket only when a later `add_internal_note(uploads=[token])` call
  carries the token there — `upload_file` alone does not attach anything.
- **New capability `ticket.attach`**, gating `upload_file`/`delete_upload`. Deliberately its
  own capability rather than folded into `ticket.write` or `ticket.note`: an upload reaches
  no existing ticket and stages bytes Zendesk holds attached to nothing, which is not
  comparable by reversibility to any capability already on the `read < note < write < reply
  < solve < close < merge` chain.
- `HttpClient.post_binary`, for the one binary-body request this surface makes. Defaults to
  `idempotent=False` — the opposite of `request()`'s default — because retrying an upload on
  a transient 503 does not repeat a no-op, it mints a *second* orphaned upload token.
- `_untrusted.wrap_upload` / `wrap_attachment`, so an upload's or attachment's provenance
  marker reads `source=zendesk-upload...` / `source=zendesk-attachment...` rather than
  borrowing `wrap_ticket`'s `zendesk-ticket...` label for data that never came from a ticket.

### Changed
- **`_http.py` split**: the retry/auth/wire half moved to a new `_transport.py`
  (`Transport`); `_http.py` keeps path safety, the envelope rule, and the public
  `get`/`request`/`post_binary` surface as a thin dispatcher onto it. No behaviour change —
  a characterisation test pins the invalid-token-retry path that this refactor touches most.
- **CI now runs `ruff check .`**, not `ruff check src tests scripts`. The old form left
  tracked `experiments/` unlinted, which was hiding a `NameError` in all three scripts that
  write to a live ticket. Exclusions now live in `pyproject.toml`, where they are reviewable.

### Fixed
- **A path-traversal in `delete_upload`, found at whole-branch review and never released.**
  `token="../tickets/159143"` issued `DELETE /api/v2/tickets/159143` — a tool gated on
  `ticket.attach` deleting a ticket, outside `CSA_ZD_ALLOWLIST_WRITE`, with the host
  unchanged so the transport's host check stayed silent. httpx normalises dot segments when
  it builds the URL. The **first fix was wrong**: it quoted the token with `safe=""`, which
  encodes `/` before `_validate_path` splits on it, so the encoding hid the traversal from
  the check meant to catch it — two guards described as independent that were in series,
  the second blinding the first. Now the token is validated as a value, against an anchored
  alphanumeric pattern, before anything encodes it; percent-escapes are refused outright at
  the choke point; and ids are coerced to decimal at the seam, since `Backend` annotates
  them `int` and mcp 2.2.0's low-level `Server` does not validate `inputSchema`.
- **`update_ticket`'s field constraint was a denylist naming five of `TicketObject`'s 65
  properties.** `collaborators` ("Users to add as cc's"), `requester`, `assignee_email`,
  `sharing_agreements`, `recipient` and `voice_comment` are all `writeOnly` in the same OAS
  schema the denylist cited, and none was on it — so a CC or a requester change reached the
  wire. Replaced with an allowlist rather than extended, because the comment shipped beside
  that denylist already said a denylist over a body the tool never enumerates is unclosable.
- **Refusal messages could hand the model a forged untrusted-data marker.** `exc.InvalidPath`
  and `exc.PolicyError` are in `server._NEVER_WRAP`, so their text reaches the model
  unwrapped as this library's own prose — and both interpolated caller-chosen strings. Ticket
  content is wrapped correctly, but a model copying a value into a tool argument would have
  the refusal launder it back as trusted. Interpolated fragments are now neutralised.
- **`add_internal_note` was retried on 503 into duplicate notes.** It appends rather than
  setting a target state, unlike its three PUT siblings; now `idempotent=False`.
- **`upload_file` accepted base64 that silently decoded to nothing.** `b64decode` discards
  non-alphabet characters before checking padding, so `b64decode("!!!!")` is `b""` with no
  error: an empty file uploaded successfully and returned a token for a zero-byte
  attachment. Now `validate=True`, plus an empty-content refusal at the backend seam.
- **Upload and attachment responses were labelled `source=zendesk-ticket…`** by a reused
  `wrap_ticket`. The markers were right; the claim beside them was false.
- **`analysis/API-SURFACE.md` §7.2b recorded the OAuth client's registered scope ceiling
  wrong.** It named four scopes; a live screenshot taken 2026-09-21 shows the client also
  carries `triggers:write`, unused by anything this block ships. No credential or code
  changed — the client's actual ceiling didn't move, the record of it was corrected to match.

### Verified live — the F1 walkthrough, 2026-09-22
This entry originally said no write tool had touched live Zendesk. **It has now.** The
walkthrough ran at rung E2 against a disposable test ticket, with both allowlists pinned to
that single id: `get_ticket`, `update_ticket`, `assign_ticket`, `upload_file`,
`add_internal_note` (with the upload attached) and `get_attachment` all succeeded against the
real API. Write-up: `experiments/2026-09-22-f1-live-walkthrough/RESULTS.md`.

The one that matters most: **`add_internal_note`'s `public: false` is now confirmed on a live
audit event**, not against `FakeBackend`. That literal is the only thing keeping a note from
emailing a customer, and until this run it had been proven exclusively against a double.

Four findings, three of which no offline test could have produced:

- **`solve_ticket` cannot solve a ticket on this tenant.** Zendesk refuses with
  a `ValidationError` naming two tenant-specific required custom fields (names withheld — they are internal field names) as required when solving —
  so the API enforces the same constraint as the agent UI *and names which fields are missing*,
  rather than failing generically. The workflow exists at E2 (`custom_fields` is on `update_ticket`'s allowlist) but
  needs the field ids, which is an admin read this rung does not have. **E2 can work a ticket
  and cannot finish one here.**
- **`priority` cannot be reset once set** — `update_ticket` is reversible to another value but
  not to unset. `analysis/tool-boundaries.csv` said `reversible` flatly; corrected.
- **The surface can assign but cannot unassign** (`TODO.md` G4). `update_ticket`'s allowlist
  excludes `assignee_id` by design and `assign_ticket` refuses an empty write — both correct
  alone, a one-way door together. `assign_ticket` also moves `status` `new`→`open` as an
  unannounced side effect, and `new` is unreachable afterwards.
- **Attachment `content_url`s are not anonymously fetchable on this tenant** — an
  unauthenticated GET returns 403. `TODO.md` G3 stays a blast-radius note rather than becoming
  a credential-bypass one. It is a tenant setting, so re-check it if that config changes.

### Still not verified
`reply_publicly` and `merge_tickets` are rung E5 and not built; `close_ticket` is deliberately
not built (closed is terminal). Whether Zendesk's plain `body` strips `display:none` text from
inbound email HTML is still open — it needs an email sent to the support address, which is a
separate experiment from this one.

## [0.1.0] — 2026-09-19

First release. **Read-only ticket triage against a live Zendesk tenant (rung E1), with
OAuth end to end and one MCP server surface — matching the description "mostly sort of
works a bit," not more.** The MCP handshake and the three data tools have been exercised
by a real client; no read tool has yet fetched a real ticket from live Zendesk (see "Not
verified" below).

### Added

**Block 0 — foundations ([#12](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/12)).**
A vertical slice through every layer for one operation, `get_ticket`:
- The typed error hierarchy and the error parser that maps Zendesk's error bodies onto it.
- `HttpClient`, with retry honouring `Retry-After` and a pagination guard.
- The `Backend` protocol seam — `ApiBackend` against the real API, `FakeBackend` for the
  offline test suite — so the whole surface is testable with no network and no credential.
- `PolicyBackend`, the fail-closed capability wrapper: an ungated method is refused, not
  silently allowed.
- A thin `ZendeskClient` over the policy-wrapped backend.
- Package skeleton, CI (lint, type-check, a Python 3.10–3.13 test matrix, coverage floor),
  and the initial `scripts/check_public_safe.py` publication gate.

**Block 0b — OAuth ([#20](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/20),
[#19](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/19),
[#27](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/27),
[#28](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/28)).**
- A public OAuth client with PKCE and no client secret ([ADR-009](DECISIONS-ADR/ADR-009.md)),
  registered against the CSA Zendesk tenant.
- The full authorization-code flow, including a loopback callback listener bound to
  `127.0.0.1` (not `localhost` — RFC 8252 §8.3) and a `--paste` path for a shell with no
  browser.
- Exactly one `0600` token file as the credential store, written atomically.
- `csa-zendesk auth login` / `auth status` / `auth whoami` / `auth logout` as a thin CLI
  door into the flow — not a product surface.
- The interim API-token auth path was removed outright ([ADR-015](DECISIONS-ADR/ADR-015.md)):
  OAuth is the only way in, and no further API tokens will be minted before Zendesk
  deactivates them fleet-wide on 2027-04-30.
- Token lifetimes requested at Zendesk's documented maxima (2-day access / 90-day refresh,
  re-requested on every refresh) paired with a real server-side revoke
  (`DELETE /api/v2/oauth/tokens/current`) — verified live that revocation invalidates the
  paired refresh token too, which is what makes maximizing the lifetimes defensible.
- OAuth verified end to end against the live tenant: login, `whoami`, and refresh have all
  been exercised for real ([#29](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/29),
  [#30](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/30)). This is the
  credential path; it is not the same claim as the read tools having been exercised — see
  "Not verified" below.

**Block 0c — the tool-surface validation slice ([#23](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/23)).**
An experiment, and it is recorded here as one: eleven tools over six operations were built
to test the design's mechanism (capability → constraint → scope → reach) before generating
the full surface. It proved the enforcement seam by mutation testing, and it found real
gaps — an unforbidden field on `update_ticket` (`TODO.md` B24), an unscoped id list on
`merge_tickets` (`TODO.md` C8) — that are still open. None of Block 0c's eleven tools are exposed by the MCP
server shipped in this release; they were a design probe, not the shipped surface. Findings
in [`analysis/SLICE-FINDINGS.md`](analysis/SLICE-FINDINGS.md).

**Block 0e — the read-only MCP server ([#31](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/31),
[#32](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/32)).** What this release
actually ships:
- `csa-zendesk-mcp`, a local stdio MCP server, installed via the `server` extra
  (`pip install -e '.[server]'`) so the library itself carries no MCP SDK dependency.
- Three read tools — `get_ticket`, `search_tickets`, `list_comments` — connected with
  exactly one capability, `TICKET_READ`, and nothing else. No write tool is registered and
  no other capability is granted; the policy gate refuses any capability this set does not
  grant, independent of what a tool table says, so the server cannot write even by mistake.
- Three auth-lifecycle tools — `authenticate`, `auth_status`, `logout` — reachable from
  inside the same session, outside the capability model by design
  ([ADR-017](DECISIONS-ADR/ADR-017.md)), so a user never has to leave the client to sign in
  or out.
- `_untrusted.py`: every requester-authorable string in a tool response is wrapped as
  untrusted data before it reaches a model, closing the prompt-injection surface a support
  ticket body is an obvious vector for.
- `CSA_ZD_ALLOWLIST_READ`, a blast-radius scope: unset means nothing is permitted, not
  "unrestricted."

### Not verified

Said plainly, because the next reader should calibrate against it: **the MCP handshake has
been proven against a real client, but no `get_ticket` call has ever fetched a real ticket
from live Zendesk.** OAuth login/whoami/refresh are the parts confirmed live; the three read
tools' actual behaviour against the live API is not (`TODO.md` F1–F5). Also not in this
release: any write capability, and the rest of the tool surface the whole-project design
calls for (~30–50 tools across the B1–B5 classification/generation track, of which three
data tools exist today).

### Fixed
- **`csa-zendesk-mcp` reported its own version as a hardcoded `"0.0.1"`**, independent of
  `csa_zendesk.__version__` — every connecting client would have been told the previous
  version, machine-readably, at the exact moment this release was announcing the opposite.
  `server.py`'s `build_server()` now imports `__version__` instead of restating it, so the
  two cannot diverge again; `test_build_server_reports_the_package_version_not_a_restated_literal`
  (`tests/test_server.py`) asserts they agree.

### Added
- **`src/csa_zendesk/py.typed`** (PEP 561), packaged via `pyproject.toml`'s
  `[tool.setuptools.package-data]`. This library is `mypy --strict` throughout; without
  this marker a type checker treats an installed copy as untyped regardless. Verified
  inside a built wheel (`python -m build --wheel` + `unzip -l`), not assumed.

### Infrastructure
- CI: lint (`ruff check`/`ruff format --check`), `mypy --strict`, a 3.10–3.13 test matrix,
  a 100%-statement coverage floor, and `scripts/check_public_safe.py` (structural-only in
  CI; the tenant-specific term list is gitignored and enforced locally).
- Branch protection on `main`: PRs required, admins enforced, no force-push or deletion.
