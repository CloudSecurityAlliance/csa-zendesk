# csa-zendesk

```
project_tracker_base: CINO Project Tracker:appf7fRQUvY9Iy7sL
project_tracker_table: Projects:tblchmbxSAavvJKaY
project_tracker_record: csa-zendesk:recvtmgqPccgLvuXz
project_source: github:CloudSecurityAlliance-Internal/CINO-Projects/projects/CloudSecurityAlliance/csa-zendesk
```

A Python library and local stdio MCP server over the Zendesk REST API, targeting **100% API
coverage**.

> **Status: Block 0 (foundations), Block 0b (OAuth) and Block 0e (a read-only MCP server) are
> complete.** `src/` holds the typed error hierarchy, the error parser, the pagination guard, the
> HTTP client with OAuth end to end (`connect()`), the `Backend` seam with an offline
> `FakeBackend`, the fail-closed capability policy, and a thin `ZendeskClient`. Three operations —
> `get_ticket`, `search_tickets`, `list_comments` — reach through every layer.
>
> **What exists: `csa-zendesk-mcp`, a stdio MCP server at rung E1** — see
> [Using the MCP server](#using-the-mcp-server) below. It connects with exactly one capability,
> `TICKET_READ`, and registers those same three operations as read-only tools, plus three
> auth-lifecycle tools (`authenticate`, `auth_status`, `logout`) that sit outside the capability
> model by design (ADR-017) so a user never has to leave the session to sign in or out.
>
> **What has not been verified: the read tools against a live ticket.** OAuth
> (login/`whoami`/refresh/revoke) and the MCP handshake have each been proven against the real
> Zendesk tenant and a real client, but no `get_ticket` call has ever fetched a real ticket —
> see `TODO.md` F1–F5. "0.1.0" (see `CHANGELOG.md`) means mostly sort of works a bit, not more.
>
> **What does not exist: everything past rung E1.** No write tool is registered and no capability
> beyond `TICKET_READ` is granted — the server cannot write even by mistake, this is a control the
> tests assert, not an oversight to note. Of the 54 tools in the whole-project design, three data
> tools are built; the rest of the write/reply/admin surface (rungs beyond E1, the B1–B5 track) is
> still to come. Do not describe any tool beyond those six as working: the Scope table below is
> the coverage target this project is building toward, not the built surface.

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
Block 0e's `csa-zendesk-mcp` (`src/csa_zendesk/server.py`) registers its six tools flat, with no
`mcp/` package and no per-family producer modules yet.

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

Registered ceiling: `read tickets:write ticket_attachments:write ticket_views:write`. `impersonate`
is deliberately absent — it is the one scope that would break the invariant that this tool can do
nothing in Zendesk that its operator could not already do.

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

**This rung is read-only.** `csa-zendesk-mcp` (the console script `src/csa_zendesk/server.py`
registers) exposes exactly three data tools — `get_ticket`, `search_tickets`, `list_comments` —
and connects with `TICKET_READ` and no other capability (`server.E1_CAPABILITIES`). It is
incapable of a write even if one were registered by mistake: `policy.py`'s gate refuses any
capability this set does not grant, independent of what the tool table lists. This is rung E1 of
the design's enablement track — *triage the live queue; propose everything, change nothing.*

**The `server` extra is not installed by default** — the library itself has no dependency on the
MCP SDK, so a consumer who only wants the typed `ZendeskClient` never pulls it in:

```bash
pip install -e '.[server]'
```

**`CSA_ZD_ALLOWLIST_READ` is not optional.** Unset never means unrestricted (`_scope.py`) — it
means nothing is permitted, so `get_ticket` and `list_comments` refuse every ticket with a
`PolicyError` until this is set, even though `search_tickets` (which carries no `subject_var`)
works fine in the meantime. That asymmetry makes the failure harder to diagnose, not easier, so
set it explicitly: `*` for the normal triage posture (see the whole-of-queue note in
`_scope.py`'s module docstring), or a comma-separated list of ticket ids to scope this install
narrowly from day one.

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
        "CSA_ZD_ALLOWLIST_READ": "*"
      }
    }
  }
}
```

`CSA_ZENDESK_SCOPES` (see the [OAuth client](#oauth-client) table above) is read at `authenticate`
time — `_cmd_authenticate`, defaulting to `read` — and is optional here for that reason: it only
matters if this install needs a browser consent scope other than the default, which read-only
triage does not.

**There is no separate login step to run first.** `authenticate`, `auth_status` and `logout` are
themselves tools, reachable from inside the session at every rung — including this read-only
one — so a user who is logged out, or whose credential has lapsed, never has to leave Claude
Code to fix it: the server's own instructions tell the model to call `authenticate` the moment
another tool reports it is not authorized. `logout` sits alongside them rather than being left to
the CLI, per [ADR-017](DECISIONS-ADR/ADR-017.md) — a surface that can acquire a credential must
also expose a way to relinquish it, reachable at least as easily as the tool that acquires it.

**Verify the install worked** before relying on it: ask the model to call `auth_status` (confirms
a token is on disk, with its expiry and granted scope, no network call), then `get_ticket` on a
ticket id you know exists. A `PolicyError` naming `CSA_ZD_ALLOWLIST_READ` at that second step means
the allowlist above is still unset or too narrow — set it and retry the same call before assuming
anything else is wrong.

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
./.venv/bin/ruff check src tests && ./.venv/bin/ruff format --check src tests
./.venv/bin/mypy --strict src
python3 scripts/check_public_safe.py
```

## License

[Apache 2.0](LICENSE).
