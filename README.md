# csa-zendesk

```
project_tracker_base: CINO Project Tracker:appf7fRQUvY9Iy7sL
project_tracker_table: Projects:tblchmbxSAavvJKaY
project_tracker_record: csa-zendesk:recvtmgqPccgLvuXz
project_source: github:CloudSecurityAlliance-Internal/CINO-Projects/projects/CloudSecurityAlliance/csa-zendesk
```

A Python library and local stdio MCP server over the Zendesk REST API, targeting **100% API
coverage**.

> **Status: Block 0 (foundations), Block 0b (OAuth), Block 0e (a read-only MCP server) and
> Block 1 (the write surface and attachments) are complete.** `src/` holds the typed error
> hierarchy, the error parser, the pagination guard, a transport with OAuth end to end
> (`connect()`), the `Backend` seam with an offline `FakeBackend`, the fail-closed capability
> policy, and a thin `ZendeskClient`.
>
> **What exists: `csa-zendesk-mcp`, a stdio MCP server at rung E2** — "work tickets for real:
> + note, write" — see [Using the MCP server](#using-the-mcp-server) below. Ten tools:
> four reads (`get_ticket`, `search_tickets`, `list_comments`, `get_attachment`), six writes
> (`update_ticket`, `assign_ticket`, `add_internal_note`, `solve_ticket`, `upload_file`,
> `delete_upload`), plus three auth-lifecycle tools (`authenticate`, `auth_status`, `logout`)
> that sit outside the capability model by design (ADR-017) so a user never has to leave the
> session to sign in or out. `reply_publicly`, `merge_tickets` and `close_ticket` are
> deliberately **not** registered — see "What does not exist" below.
>
> **What has not been verified: any write, against a live ticket.** The read path — OAuth,
> the MCP handshake, the allowlist failing closed, ticket text wrapped as untrusted data — was
> confirmed live against the real Zendesk tenant on 2026-09-21, from a separate, unmodified
> clone at `0.1.0`. **No tool in the write surface (`update_ticket`, `assign_ticket`,
> `add_internal_note`, `solve_ticket`, `upload_file`, `delete_upload`) has been exercised
> against live Zendesk — Block 1 was built and reviewed entirely offline, against `FakeBackend`
> and mocked transports.** The live end-to-end write walkthrough is `TODO.md` F1 and happens
> after this branch merges, with a human present; do not read "Block 1 is complete" as "Block 1
> is live-verified" — they are separate claims.
>
> **What does not exist: reach, admin, and the rest of the B1–B5 track.** `reply_publicly`
> (public reply) and `merge_tickets` both email a requester and are held for rung E5
> (`CSA_ZD_ALLOW_REACH`); `close_ticket` is terminal (`analysis/API-SURFACE.md` §5.4d — Zendesk
> accepts `status: "closed"` and never lets it change again) and is held back deliberately, not
> because it is unbuilt. No admin capability (`ADMIN_READ`/`ADMIN_WRITE`) is granted. Of the 54
> tools in the whole-project design, ten are built; the rest is still to come. Do not describe
> any tool beyond those ten as working: the Scope table below is the coverage target this
> project is building toward, not the built surface.

## Scope

| Capability | Operations | 1.0.0 |
|---|---:|---|
| Ticketing | 640 | yes |
| Help Center | 182 | yes |
| Status | 3 | yes |
| Voice (Talk) | 60 | post-1.0 |
| Live Chat, Messaging, AI Agents, Custom Data, Sales CRM | no published spec | post-1.0 |

**822 of 882 machine-readable operations at 1.0.0** — which is coverage of the published
specs, not of the API. The 60 deferred are all Voice. Status's three operations have no
published spec and are not in the inventory at all, so they sit outside both figures — which
is what made the earlier count wrong: 825 counted them in a numerator measured against a
denominator that excludes them. Guarded by `scripts/check_counts.py`.
The Help Center spec describes roughly 18 of ~30 documented families;
about a dozen, several of them live, have no spec entry and need hand-written methods. See
`analysis/API-SURFACE.md` §4b.

## Why this exists

The third-party Zendesk MCP servers are thin. The most popular — 115★, actively maintained,
genuinely well built — ships **7 tools**. The largest ships ~47. Against 882 operations.

More consequentially, the whole field shares four weaknesses:

- **No capability policy.** OAuth scopes, fixed at client registration, are the only control.
  Zendesk has **54 granular scopes**, which gives a local fail-closed policy layer something real
  to bind to.
- **Offset pagination everywhere.** Zendesk caps offset paging at 100 pages / 10,000 records and
  then returns HTTP 400. Large result sets truncate, and the tools present the truncation as an
  answer.
- **API-token auth.** Zendesk **permanently deactivates all API tokens on 2027-04-30**, and no
  account can create a new one after 2026-10-27.
- **Pre-2.0 MCP SDK.** Hand-rolled `list_tools`/`call_tool` dispatch means no structured output
  and no tool annotations, so a client cannot tell a read from a destructive write.

## Architecture

Third in the line after [`csa-skilljar`](https://github.com/CloudSecurityAlliance/csa-skilljar)
and `csa-google-workspace`, on the same spine:

```
Backend (Protocol)   the seam - keyword-only args, returns raw upstream envelopes
    ^ wrapped by
PolicyBackend        capability gating; FAILS CLOSED - an ungated method is refused
    ^ consumed by
ZendeskClient        thin typed library surface (the public product)
    ^ consumed by
mcp/_tools/*.py      per-family register_*(app, get_client) producers
```

`mcp/_tools/*.py` is the target layout for the full 54-tool surface, not what exists today:
`csa-zendesk-mcp` (`src/csa_zendesk/server.py`) registers its thirteen tools flat, with no `mcp/`
package and no per-family producer modules yet.

Enforcement lives in the wrapper around the seam, not in the tools, so a library embedder gets
the same guarantee an MCP client does.

## What is here

| Path | What |
|---|---|
| `specs/` | Three upstream OpenAPI snapshots + `PROVENANCE.md` (URLs, sha256) |
| `analysis/API-SURFACE.md` | **Start here.** The enumeration and the probe findings |
| `analysis/operation-inventory.csv` | 882 rows, one per operation |
| `analysis/family-probe.json` | 49 live availability probes |
| `scripts/inventory.py` | Regenerates the inventory from `specs/` |
| `scripts/probe_families.py` | Re-runs the live probes (GET only) |
| `scripts/probe_access.py` | Access audit: what the credential actually reaches |
| `docs/superpowers/specs/` | **The design.** Start here before proposing anything |
| `TODO.md` | **The index of all open work.** Start here for what is unfinished |
| `DECISIONS-ADR.md` | Decision log index; entries in `DECISIONS-ADR/` |
| `WAITING-FOR.md` | Conditions with observable triggers; entries in `WAITING-FOR/` |

Zendesk publishes the OpenAPI specs but links to none of them; all three were found by probing
URL shapes. They are snapshots of someone else's moving target — re-fetch and diff before
trusting them.

## Findings that constrain the design

Six are recorded in `analysis/API-SURFACE.md`. The three that change how the code must be
written:

1. **`users/me.json` returns HTTP 200 with `"name": "Anonymous user"` when wholly
   unauthenticated.** It serves anonymous Help Center visitors, so it degrades instead of
   refusing — and it is the endpoint every client uses as a credential health check. Validating
   against it reports healthy for a missing credential. *Credential validation probes a resource
   endpoint and asserts a non-null `user.id`.*
2. **Search takes offset pagination only and stops at 1000 results**, while reporting a `count`
   of six figures. A tool that surfaces `count` as if the caller could page to it is lying.
   `search/export` is the cursor-paginated, uncapped alternative.
3. **Pagination cannot be generated from the specs.** The Help Center spec declares paging on 0
   of 96 GET operations, yet cursor paging demonstrably works. The generator must not read
   silence as "unpaginated".

## Deliberate exclusions

Six families — 47 operations, 5% of the surface — are **out of scope and will not be built**:
IT Asset Management, Group SLA Policies, Workspaces, Ticket Form Statuses, Audit Logs, and
Help Center Service Catalog Items. The development account cannot reach them (403: a plan
boundary), so they cannot be tested, and this project does not ship API code it has never
called. See [ADR-001](DECISIONS-ADR/ADR-001.md) for the reasoning and
[WAITING-FOR-001](WAITING-FOR/WAITING-FOR-001.md) for what would reopen it.

This does not relax the error layer: plan boundaries differ per account, so any deployment can
meet a 403 on an endpoint we *did* implement, and the taxonomy must say "your plan does not
include this" rather than "this is broken".

## Configuration

**The library authenticates by OAuth and by nothing else** ([ADR-015](DECISIONS-ADR/ADR-015.md)).
`HttpClient` takes a `token_provider` callable and sends a `Bearer` header; there is no API-token
code path, no fallback, and no environment variable the library reads. A fallback that silently
activates when OAuth is misconfigured turns an auth failure into something that reads like a
permissions failure, which is the confusion the 401 handling goes out of its way to prevent.

### OAuth client

Registered in Zendesk Admin Center (**Apps and integrations › APIs › OAuth clients**) as
`csa-zendesk`, 2026-09-18. Redirect URIs are the three loopback candidates the callback listener
binds, in order; the client's scope list is a **ceiling**, and what a token actually receives is
whatever `CSA_ZENDESK_SCOPES` requests within it.

| Local variable | Zendesk's own label | What it is |
|---|---|---|
| `CSA_ZENDESK_SUBDOMAIN` | Subdomain | the `<subdomain>` in `https://<subdomain>.zendesk.com`, with no scheme and no suffix |
| `CSA_ZENDESK_MCP_SERVER_IDENTIFIER` | **Identifier** | the OAuth `client_id` — `csa-zendesk`. Not a secret |
| `CSA_ZENDESK_MCP_SERVER_SECRET` | **Secret** | issued to every client regardless of kind. **Retained, unused** — see [API-SURFACE §7.3](analysis/API-SURFACE.md) |
| `CSA_ZENDESK_SCOPES` | scope (request) | space-separated, defaults to `read`. Must be a subset of the ceiling |
| `CSA_ZENDESK_TOKEN_FILE` | — | override for the `0600` token file ([ADR-009](DECISIONS-ADR/ADR-009.md)) |

**Why the names are long.** They are local names, not vendor names, and they are explicit on purpose:
one machine runs many CSA projects against many vendors, so a variable has to say *which project*,
*which role*, and *which vendor* without context. Where a name maps to something an operator reads
off a vendor screen, it takes the vendor's own label for the last segment — Zendesk calls the client
id the **Identifier**, so the variable does too, and nobody has to translate while looking at the
form.

Registered ceiling: `read tickets:write ticket_attachments:write ticket_views:write
triggers:write` (corrected 2026-09-21 — a live screenshot showed `triggers:write` was already on
the client and missing from this line; see `analysis/API-SURFACE.md` §7.2b). `triggers:write` is
not requested by anything this project ships at rung E2; it sits on the ceiling unused until an
admin-configuration tool needs it. `impersonate` is deliberately absent — it is the one scope that
would break the invariant that this tool can do nothing in Zendesk that its operator could not
already do.

### Getting a token

`csa-zendesk` (`src/csa_zendesk/cli.py`) is a small console script, a door into OAuth rather than
a product: `auth login` runs the flow once and persists the result — opening a browser, or
printing a URL to paste back with `--paste` on a remote shell with no browser of its own —
`auth status` reports whether a token file exists, its path, its expiry and its granted scope
without a network call, `auth whoami` confirms live which Zendesk identity it resolves to, and
`auth logout` revokes the stored token server-side and then clears the local file. All four print
human-facing text to stderr except `whoami`'s and `status`'s own answer, which goes to stdout
since either might reasonably be piped; none of the four can print the token itself.

**Token lifetimes are requested at their documented maxima, on every login and every refresh:**
`expires_in` at 172,800 seconds (2 days) and `refresh_token_expires_in` at 7,776,000 seconds (90
days) — the ceilings Zendesk's OAuth token endpoint documents, not arbitrary choices (see
`_flow.MAX_ACCESS_TOKEN_LIFETIME_SECONDS` / `MAX_REFRESH_TOKEN_LIFETIME_SECONDS`). Both fields are
resent on every refresh, not just at login, because Zendesk rotates the refresh token on every use
(single-use, confirmed against the live tenant): re-requesting the maximum each time makes the
90-day window slide forward instead of shrinking back to Zendesk's 30-day default on first refresh.
This is deliberately paired with `auth logout`: both tokens already live in the same `0600` file, so
a short access-token lifetime buys nothing against file theft while costing a refresh every 30
minutes instead — maximising lifetimes without a real revoke path would be careless (TODO.md E11,
E15). `auth logout` revokes the access token via `DELETE /api/v2/oauth/tokens/current`; **this also
invalidates the paired refresh token** — not stated by Zendesk's API spec, but confirmed 2026-09-19
against the live tenant (TODO.md E20, `analysis/API-SURFACE.md` §7.4). This is what makes the
maximal lifetimes above defensible: a stolen token file does not survive a `logout`.

The **research scripts under `scripts/`** — `zd.py`, `ui_actions.py`, `probe_families.py`,
`probe_access.py` — which refresh `analysis/` and ship in no package, authenticate the same way
as everything else: **OAuth, through the token file above** ([ADR-009](DECISIONS-ADR/ADR-009.md)),
using the same `CSA_ZENDESK_SUBDOMAIN` and `CSA_ZENDESK_MCP_SERVER_IDENTIFIER` variables. `./.env`
is **not a credential source for anything in this repo** ([ADR-015](DECISIONS-ADR/ADR-015.md)) —
the interim API-token path (`CINO_CSA_ZENDESK` + `CINO_CSA_ZENDESK_EMAIL`, basic auth as
`EMAIL/token:TOKEN`) was removed once the scripts were ported off it. An operator's old token may
still physically sit in a local `./.env`; nothing here reads it, and removing it is the operator's
own call.

```bash
export CSA_ZENDESK_SUBDOMAIN=<subdomain>
export CSA_ZENDESK_MCP_SERVER_IDENTIFIER=<client-id>
csa-zendesk auth login               # once, per operator - opens a browser
python3 scripts/inventory.py         # 882 operations
python3 scripts/probe_families.py    # 43/49 families reachable (as last measured, under the API-token path)
```

## Using the MCP server

**This rung is E2 — "work tickets for real: + note, write."** `csa-zendesk-mcp` (the console
script `src/csa_zendesk/server.py` registers) exposes thirteen tools — ten that touch ticket
data, plus the three auth-lifecycle tools (`authenticate`, `auth_status`, `logout`). The ten:
four reads — `get_ticket`,
`search_tickets`, `list_comments`, `get_attachment` — and six writes — `update_ticket`,
`assign_ticket`, `add_internal_note`, `solve_ticket`, `upload_file`, `delete_upload` — and
connects with `TICKET_READ`, `TICKET_WRITE`, `TICKET_NOTE`, `TICKET_SOLVE` and `TICKET_ATTACH`
(`server.E2_CAPABILITIES`), nothing more. `reply_publicly`, `merge_tickets` and `close_ticket`
are not registered at all — a tool the model can see but must not use is worse than one that is
simply absent — so no capability grant here can reach them. `policy.py`'s gate refuses any
capability `E2_CAPABILITIES` does not grant, independent of what the tool table lists.

**`get_attachment` is a read, not a write**, even though it is documented here and not above: it
gates on `TICKET_READ`, the same capability rung E1 already grants, so it works at E1 too —
reading a ticket's attachments is no more privileged than reading the ticket itself. It is also
scoped by *neither* allowlist: an attachment is named by `attachment_id`, not `ticket_id`, so
there is no ticket for `CSA_ZD_ALLOWLIST_READ` to check against — only the capability gates it.

**The `server` extra is not installed by default** — the library itself has no dependency on the
MCP SDK, so a consumer who only wants the typed `ZendeskClient` never pulls it in:

```bash
pip install -e '.[server]'
```

### E2 needs two things E1 did not, and neither is guessable

**1. The token must carry write scope, and the client must be re-authenticated.** A token
minted for E1 requested only `read`. Every one of the six write tools calls a `PUT`/`POST`/
`DELETE` endpoint, and a read-scoped token fails all of them with a plain **403** — which reads
exactly like a permissions misconfiguration, not like "this token was never asked for write."
Widen `CSA_ZENDESK_SCOPES` before re-running login:

```bash
export CSA_ZENDESK_SCOPES='read tickets:write ticket_attachments:write'
csa-zendesk auth login   # re-run — a wider scope only takes effect on a fresh grant
```

`tickets:write` covers `update_ticket`/`assign_ticket`/`add_internal_note`/`solve_ticket` (all
`PUT /api/v2/tickets/{id}`, constrained per-tool by `tools.py`, not by the scope);
`ticket_attachments:write` covers `upload_file`/`delete_upload` (`/api/v2/uploads`). Both are
within the OAuth client's registered ceiling (`read tickets:write ticket_attachments:write
ticket_views:write triggers:write` — see [OAuth client](#oauth-client) above); requesting
anything outside that ceiling fails closed with `400 invalid_scope` at login, not silently.
`auth_status` reports the token's granted scope with no network call — check it after
re-running login if a write still 403s.

**2. `CSA_ZD_ALLOWLIST_WRITE` must name the ticket ids writes may touch, and unset permits
nothing.** Exactly the same shape as `CSA_ZD_ALLOWLIST_READ` at E1, and the same failure mode
that was a **Critical finding in Block 0e**: unset does not mean unrestricted, it means every
write is refused with a `PolicyError` and no clue why, since the token, the capability grant and
the tool registration are all otherwise correct. Set it explicitly — `*` to permit every ticket,
or a comma-separated list of ticket ids to scope this install narrowly (the safer default for a
write-capable install, unlike the read side's usual `*`-for-triage posture):

```bash
export CSA_ZD_ALLOWLIST_WRITE='<ticket-id>,<ticket-id>'
```

`upload_file`, `delete_upload` and `get_attachment` are **not** scoped by either allowlist —
none of the three takes a `ticket_id` (an upload is not yet attached to any ticket; an
attachment is named by its own id) — so `CSA_ZD_ALLOWLIST_WRITE` governs exactly the other four
write tools, the ones that act on a named ticket.

**Know what that means for `get_attachment` before you rely on the allowlist.** An install
pinned to one ticket can still read the content of *any* attachment in the tenant, because an
`attachment_id` does not say which ticket it belongs to and this server does not go looking.
Attachments are where the sensitive material usually is, so this is the one place the allowlist
does not deliver what it otherwise does. It is not a hole in a security boundary — the real
boundary is the OAuth token's own scope, and anyone holding this credential could open the same
attachment in the Zendesk UI by hand — but it *is* a hole in the blast-radius narrowing that is
the whole reason to set an allowlist. There is no setting that turns this one tool off — `E2_CAPABILITIES` is fixed in
the code — so if it matters for your install, the only remedy available today is not to grant
this server the credential. `TODO.md` G3 tracks the decision about scoping it properly.

One further thing, which changes the size of this rather than its shape: `get_attachment`
returns a `content_url`, and **Zendesk attachment content URLs are fetchable without
authentication** unless the tenant has enabled *"require authentication to download
attachments"* (it is off by default). So the exposure is not only "whoever holds this
credential can read any attachment" — it is that the server emits a URL anything else with
sight of the model's context can fetch, outside the credential entirely. Check that tenant
setting before relying on this rung.

**`CSA_ZD_ALLOWLIST_READ` is still not optional** (unchanged from E1): unset means nothing is
permitted for `get_ticket`/`list_comments`, even though `search_tickets` and `get_attachment`
(neither carries a `subject_var`) work regardless. Set both allowlists explicitly rather than
relying on this asymmetry.

Then register the server with Claude Code. The registration name is **`csa-zendesk`** — a
different namespace from the executable, matching the rest of this fleet (`csa-google-workspace`,
`csa-skilljar`, `customer360`, `firecrawl` — none carries an `-mcp` suffix) — and it is what
prefixes every tool the model sees, so `get_ticket` shows up as `mcp__csa-zendesk__get_ticket`.
`-s user` registers it for every session rather than binding it to one project directory —
without it (the default, `local` scope), running this from inside a git worktree resolves to the
worktree's *parent* repository, so the server registers against a path you didn't type and never
shows up in the session you're working in:

```bash
claude mcp add csa-zendesk -s user \
  -e CSA_ZENDESK_SUBDOMAIN=<subdomain> \
  -e CSA_ZENDESK_MCP_SERVER_IDENTIFIER=<client-id> \
  -e CSA_ZD_ALLOWLIST_READ='*' \
  -e CSA_ZD_ALLOWLIST_WRITE='<ticket-id>,<ticket-id>' \
  -- /abs/path/to/csa-zendesk/.venv/bin/csa-zendesk-mcp
```

Use an **absolute path** to the installed `csa-zendesk-mcp` executable, not the bare command
name — from a source checkout it lives in that checkout's own venv, and a bare name resolves
through `PATH`, which may find a different install or none at all. The equivalent
`claude_desktop_config.json` stanza (the JSON key is the registration name, `csa-zendesk`, not
the executable):

```json
{
  "mcpServers": {
    "csa-zendesk": {
      "command": "/abs/path/to/csa-zendesk/.venv/bin/csa-zendesk-mcp",
      "env": {
        "CSA_ZENDESK_SUBDOMAIN": "<subdomain>",
        "CSA_ZENDESK_MCP_SERVER_IDENTIFIER": "<client-id>",
        "CSA_ZD_ALLOWLIST_READ": "*",
        "CSA_ZD_ALLOWLIST_WRITE": "<ticket-id>,<ticket-id>"
      }
    }
  }
}
```

`CSA_ZENDESK_SCOPES` (see the [OAuth client](#oauth-client) table above) is read at `authenticate`
time — `_cmd_authenticate`, defaulting to `read` — which is why widening it and re-running
`auth login` (above) is a step of its own, not something this registration triggers on its own.

**There is no separate login step to run first.** `authenticate`, `auth_status` and `logout` are
themselves tools, reachable from inside the session at every rung — including before this one
has a working credential — so a user who is logged out, or whose credential has lapsed, never
has to leave Claude Code to fix it: the server's own instructions tell the model to call
`authenticate` the moment another tool reports it is not authorized. `logout` sits alongside them
rather than being left to the CLI, per [ADR-017](DECISIONS-ADR/ADR-017.md) — a surface that can
acquire a credential must also expose a way to relinquish it, reachable at least as easily as the
tool that acquires it.

**Verify the install worked** before relying on it: ask the model to call `auth_status` (confirms
a token is on disk, with its expiry and granted scope, no network call — check the scope here
first if a write is about to 403), then `get_ticket` on a ticket id you know exists. A
`PolicyError` naming `CSA_ZD_ALLOWLIST_READ` or `CSA_ZD_ALLOWLIST_WRITE` at that step, or at a
write, means the corresponding allowlist above is still unset or too narrow — set it and retry
the same call before assuming anything else is wrong. A plain `403` on a write (not a
`PolicyError`) means the token itself lacks the scope — re-check `auth_status`'s reported scope
against the two above.

### Uploading and attaching a file — two steps, not one

`upload_file` alone does **not** put a file on a ticket. It sends the file's bytes to Zendesk and
gets back a token naming bytes that exist on Zendesk's side attached to *nothing* — invisible
everywhere else in this server's surface, including `get_ticket` and `list_comments` on the
ticket you meant to attach it to. Calling only `upload_file` and stopping looks like it did
nothing, because from the ticket's point of view it did. The file becomes visible on a ticket
only on a **second** call, `add_internal_note(ticket_id=..., uploads=[token])`, which carries the
token onto the ticket as a comment attachment. `delete_upload(token=...)` exists specifically to
clean up a token from a failed or abandoned first step — an unattached upload is litter nothing
else in this surface will ever show you, so without a deliberate `delete_upload` call it stays on
Zendesk's side indefinitely.

`upload_file`'s bytes travel over MCP as `content_base64` — a base64-encoded string, not raw
bytes or a file path — because MCP tool arguments are JSON, which has no binary type. Decoding
uses `validate=True`, so malformed base64 is refused with an error rather than silently decoding
to truncated or empty bytes. `upload_file` also refuses empty content outright, which is not the
ordinary empty-write refusal every other write tool has: Zendesk *accepts* a zero-byte upload and
hands back a real, usable-looking token for it, so without this refusal the failure would be
silent — a token that names an attachment which downloads as nothing.

## Development

```bash
python3 -m venv .venv
./.venv/bin/pip install -e '.[dev,server]'
```

`server` is an optional extra (`[project.optional-dependencies]`), not a hard dependency — the
library stays importable without the MCP SDK. Install it anyway in a dev environment: it backs
`src/csa_zendesk/server.py` (the `csa-zendesk-mcp` console script), and
`tests/test_public_api.py`'s import-time stdout guard imports every module in the package,
`server.py` included, so the test suite fails to collect without it.

```bash
./.venv/bin/pytest --cov=csa_zendesk --cov-fail-under=100 -q
./.venv/bin/ruff check .
./.venv/bin/ruff format --check src tests
./.venv/bin/mypy --strict src
python3 scripts/check_public_safe.py
python3 scripts/check_boundaries.py
```

**`ruff check .` lints the whole tree, not a directory list.** `ruff check src tests scripts`
once left tracked-but-unnamed `experiments/` unlinted, hiding a `NameError` in all three scripts
that write to a live ticket. Anything that needs to be exempted from lint or format is an
exclusion in `pyproject.toml`, where it is reviewable — not an absence from the command line.
`check_boundaries.py` fails if a tool is not bucket-pure: every operation shared by more than one
tool must carry a real constraint distinguishing them (ADR-016).

## License

[Apache 2.0](LICENSE).
