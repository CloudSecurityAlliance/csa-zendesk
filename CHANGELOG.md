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

## [Unreleased]

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

### Infrastructure
- CI: lint (`ruff check`/`ruff format --check`), `mypy --strict`, a 3.10–3.13 test matrix,
  a 100%-statement coverage floor, and `scripts/check_public_safe.py` (structural-only in
  CI; the tenant-specific term list is gitignored and enforced locally).
- Branch protection on `main`: PRs required, admins enforced, no force-push or deletion.
