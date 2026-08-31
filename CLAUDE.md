# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this repository is

`csa-zendesk` — a Python library (import name `csa_zendesk`) and local stdio MCP server over
the Zendesk REST API, targeting 100% coverage of what is in scope and reachable.

**Status: research complete, nothing implemented.** There is no `src/` yet. The repository
holds upstream API snapshots, an operation inventory, an anonymised ecosystem survey, live
experiment results, and the decisions taken so far. **Do not describe any feature as working.**

Third in the line after [`csa-google-workspace`](https://github.com/CloudSecurityAlliance/csa-google-workspace)
and [`csa-skilljar`](https://github.com/CloudSecurityAlliance/csa-skilljar), and intended to
share their architecture.

## Where things live

- **`TODO.md`** — the index of ALL open work, per the CINO todo-index convention. Sweep this
  plus open Issues and you have found everything. It also carries a **consideration pile** of
  things deliberately not committed to — read it before proposing a "missing" feature.
- **`analysis/API-SURFACE.md`** — start here. What the API is, what this credential reaches,
  and the probe-verified behaviours that constrain the design.
- **`analysis/UI-ACTION-MAP.md`** — the tool surface derived from what the agent web interface
  actually offers, rather than from the API's own shape.
- **`analysis/PRIOR-ART.md`** — twelve MCP servers, read from source, **anonymised on purpose**.
- **`analysis/OFFICIAL-CLIENTS.md`** — what Zendesk's own Ruby and PHP clients know, including
  several things the spec does not say. §7 is the list of behaviours to take.
- **`analysis/CLIENT-LIBRARIES.md`** — the build-vs-adopt assessment.
- **`analysis/operation-inventory.csv`** — 882 operations. **The authoritative path list.**
- **`specs/`** — three upstream OpenAPI snapshots with provenance and sha256s. Snapshots of
  someone else's moving target.
- **`DECISIONS-ADR.md`** + **`DECISIONS-ADR/`** — ADR-001 (do not build what cannot be tested)
  and ADR-002 (build from scratch) are both load-bearing. Do not contradict them without a
  superseding ADR.
- **`WAITING-FOR.md`** + **`WAITING-FOR/`** — conditions with observable triggers.
- **`experiments/`** — runnable probes with a dated `RESULTS.md`. Probe beats docs.
- **`scripts/`** — everything in `analysis/` is regenerable. Nothing there is hand-maintained.

## Critical architectural facts

1. **The seam returns raw envelopes.** `Backend` hands back upstream JSON unshaped; the MCP tool
   layer turns it into `TypedDict`s. This is why one uniform `PolicyBackend` wrapper can gate
   every method, and it is why ADR-002 rejects every object-mapping library.
2. **Enforcement wraps the seam; it is not a check in the tools.** A library embedder gets the
   same guarantee an MCP client does.
3. **The gate table fails closed.** A `Backend` method with no declared capability is *refused*,
   not delegated — so forgetting to declare one turns a feature off rather than leaving a hole.
4. **Tool names are bare `verb_noun`**, never `zendesk_`-prefixed. The client namespaces by
   server already, and the survey settled it.
5. **Six names are fixed by the field** and must not be renamed: `get_ticket`, `create_ticket`,
   `update_ticket`, `get_user`, `get_organization`, `get_ticket_comments`.
6. **`update_ticket` is deliberately broad.** The web UI stages every field edit, the comment
   and the status change and applies them in one `PUT`. One tool matching that is faithful to
   both the UI and the API, not a lumping-together.

## Invariants that fail silently — check these when editing

Every one is probe-verified against the live API, and every one returns a success or a
plausible answer while being wrong.

1. **`users/me.json` answers HTTP 200 with an "Anonymous user" object when wholly
   unauthenticated.** It serves anonymous Help Center visitors. It is also the endpoint every
   client reaches for as a credential health check. **Validate credentials against a resource
   endpoint and assert a non-null `user.id`** — never against `users/me`.
2. **Search reports a `count` far larger than it will return.** Offset-only, and it refuses past
   1000 results. A tool that surfaces `count` as if the caller could page to it is lying;
   `search/export` is the cursor-paginated alternative.
3. **Never emit both pagination styles on one request.** With a cursor page parameter present,
   `sort_by`/`sort_order` are **silently discarded** and you get default order with a 200.
   Zendesk's own PHP client strips them; so must we. Refuse rather than trust the caller.
4. **Cursor paging restricts sorting** to `updated_at`, `id`, `status`. `created_at` is *not*
   cursor-sortable, so "newest tickets" has no direct expression — `sort=-id` is the proxy and
   translating it is the tool layer's job.
5. **Offset paging dies at 10,000 records** with a typed `InvalidPaginationDepth`.
6. **Errors arrive in four incompatible envelopes**, and on a validation refusal everything
   useful is in `details` — which is a **map from field name to a list of problems**, not a
   fixed shape. Whole-record problems land under `base`; field-scoped ones land under the field
   (`details.status[]`). **Iterate the keys of `details`; never index one** — a parser hardcoded
   to `details.base` finds nothing on a field-scoped error. A parser reading only `error` and
   `description` gets "RecordInvalid / Record validation errors" and discards the diagnosis
   entirely. Zendesk's own Ruby client reads `details` first, then falls back across four keys.
12. **`status: "closed"` is accepted by the API and is terminal.** A closed ticket refuses every
   further write, including reopening, and the web UI does not offer Closed in its status picker
   at all. It must be gated separately from both writes and `solve`, and its tool description
   must say it cannot be undone.
7. **`404` means two different things.** `InvalidEndpoint` is "not on this account";
   `RecordNotFound` is "wrong id". Collapsing them loses the plan-versus-id distinction.
   Similarly a `403` is a plan boundary, not an outage — the official clients get this wrong.
8. **`503` is retryable** alongside `429`. Honour `Retry-After`; default to 10s when absent.
9. **Rate limits are layered.** An account limit plus much tighter per-endpoint buckets
   (incremental exports is an order of magnitude tighter). A limiter watching only the account
   header will trip a bucket long before it expects to.
10. **Pagination cannot be generated from the specs.** The Help Center spec declares it on 0 of
    96 GETs that demonstrably paginate. Detect the style from the response — `meta` + `links` —
    and never read silence as "unpaginated".
11. **A 404 from a guessed path is not evidence of anything.** This was violated twice, both
    times reporting a family as absent when it was plan-gated or available at a different path.
    `analysis/operation-inventory.csv` is the authoritative path list; generate probes from it.

Plus the MCP SDK traps inherited from the sibling projects, all verified against `mcp` 2.1.0:
nothing may touch **stdout** under stdio; raise the SDK's **`ToolError`** or your message is
discarded; **never `Field(alias=…)`** on a tool parameter; **`mcp.server.fastmcp` does not
exist** (it is `from mcp.server import MCPServer`); sync handlers run on **worker threads**, so
any non-thread-safe client must be thread-local; **`TypedDict` from `typing_extensions`** below
Python 3.12; and do not block `initialize` on a network call.

## The public/private line — enforced, not remembered

**Facts about Zendesk are public. Facts about a tenant are not.** Vendor API behaviour helps
whoever hits it next; an organisation's process fields, group names, volumes and macro estate
are its own business. The distinction is *not* "is it a credential".

A survey of an ecosystem also **names no individuals**. `analysis/PRIOR-ART.md` aliases twelve
servers A–L; most are individual side projects given away for free, and ranking them by name in
a corporate repository would be unkind and beside the point.

`scripts/check_public_safe.py` enforces both, in two tiers:

- **Structural patterns** live in the script and name nobody — shapes like an email address, a
  real tenant subdomain, `<number> macros`, an `/agent/tickets/<id>` URL.
- **Literal terms** live in `tenant-config/private-terms.txt`, gitignored and loaded at
  runtime — because an earlier version hardcoded the literals and thereby became a compact,
  searchable index of exactly what it existed to hide. **The denylist is the disclosure.**

With the private list absent it reports `STRUCTURAL ONLY` rather than a clean bill of health it
cannot support. `.githooks/pre-commit` runs it; enable with
`git config core.hooksPath .githooks`. Mutation-test it after changing the patterns.

**Never commit:** `.env`, the tenant-config extract, probe output containing rows, or clones of
other people's repositories. `.gitignore` covers the known shapes; the judgement calls are
yours.

## Data hygiene

Ticket bodies are **customer correspondence** — real names, addresses, and whatever a requester
pasted in. Probe artifacts record **counts and shapes, never rows**. The reference tenant runs
quality-management and incident-response workflows on Zendesk, so ticket content can include
audit evidence.

**Prompt injection through ticket content is the named primary risk.** A ticket body is text
written by a stranger with an interest in the outcome, and reading it is the entire point of the
product. Nothing in the field addresses this except one surveyed server; see `TODO.md` A4.

## Zero defect — surface failures, never suppress them

Follows [ZERO-DEFECT.md](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/ZERO-DEFECT.md).
The principles that have already earned their place here:

- **ZD-2, generate errors aggressively.** A 200 that looks wrong is an error. Four of the
  invariants above are 200s that were wrong.
- **ZD-17, silence is not health.** Ask of any branch that concludes there is no work: *if this
  fired forever, would anyone notice?* The `users/me` health check is exactly that shape.
- **Every guard gets mutation-tested once.** Break the thing it protects, watch it fail, restore.
  `check_public_safe.py` was mutation-tested three ways, and the third revealed it reported full
  coverage when its private term list was missing.
- **A test that passes for the wrong reason is worse than no test.** The requirements model
  matched a live 422 exactly — on a form with zero conditional rules, so the match validated only
  the trivial half. See `TODO.md` C1.

## Commands

**Always work inside a virtual environment.** Never `pip install` into a system Python.

```bash
set -a; . ./.env; set +a          # CINO_CSA_ZENDESK + CINO_CSA_ZENDESK_EMAIL
export ZENDESK_SUBDOMAIN=<subdomain>   # no default, deliberately

python3 scripts/check_public_safe.py   # the publication gate; run before every push
python3 scripts/inventory.py           # regenerate the operation inventory from specs/
python3 scripts/probe_access.py        # what this credential reaches, GET only
python3 scripts/survey_tools.py        # re-run the ecosystem survey from the clones
python3 scripts/extract_config.py      # tenant configuration -> gitignored tenant-config/
```

Credentials come from `./.env`. The current one is an **API token**: unscoped, full admin, and
it bypasses account 2FA. Zendesk deactivates all API tokens on **2027-04-30**, and no account
can create one after **2026-10-27**. The shipped design authenticates with OAuth.

## Working in this repo

- **Branch + PR for every change**; never commit to `main`. Conventional prefixes
  (`feat/`, `fix/`, `docs/`, `chore/`, `test:`, `security:`).
- **Probe before asking.** If a question can be answered by a free, read-only, side-effect-free
  call, make the call. Most of `analysis/` came from doing this.
- **Probe beats docs.** Where the spec and a probe disagree, the probe wins and the finding is
  written down. This has happened repeatedly — pagination, error shapes, missing families.
- **Read paths from the inventory, not from memory.** See invariant 11.
- **GET only against the live API** unless the user has authorised a specific write. Never
  infer a field value to satisfy a validator; that fabricates audit evidence.
- **Do not describe unimplemented things as working.** The README carries a status banner.
