# AGENTS.md

Tightened for modern AI: 2026-11-12.

Instructions for every AI agent working in this repository. `CLAUDE.md` and `GEMINI.md` are
one-line pointers to this file: **do not edit them.** Put every instruction here, and label one
that applies to a single tool with that tool's name
([DEC-033](https://github.com/CloudSecurityAlliance-Internal/CINO-Engineering/blob/main/DECISIONS.md#dec-033-agentsmd-is-the-instruction-file-claudemd-and-geminimd-are-one-line-pointers-never-full-files)).

`csa-zendesk` (import name `csa_zendesk`): a Python library and local stdio MCP server over the
Zendesk REST API, targeting 100% coverage of what is in scope and reachable. Status lives in
`CHANGELOG.md`, and what is built in `server.TOOLS`, not here. Which fleet decisions this server
adopts, and the two it deviates from: [DECISIONS-INHERITED.md](DECISIONS-INHERITED.md); check it
before assuming a fleet default applies.

## Rules

- **Branch + PR for every change; never commit to `main`.** Prefixes `feat/`, `fix/`, `docs/`,
  `chore/`, `test:`, `security:`.
- **Do not describe unimplemented things as working.** What is built is `server.TOOLS`
  (`READ_TOOLS + WRITE_TOOLS + AUTH_TOOLS + CONFIG_TOOLS`), plus `REACH_TOOLS` only when
  `CSA_ZD_ALLOW_REACH=true`; the rest of the design is not built. Read counts from the code: a
  number in prose is a claim that goes stale.
- **GET only against the live API** unless the user has authorised a specific write. **Never infer
  a field value to satisfy a validator;** that fabricates audit evidence.
- **Probe before asking:** if a free, read-only, side-effect-free call answers the question, make
  it. **Probe beats docs:** where the spec and a probe disagree, the probe wins and the finding is
  written down.
- **Read paths from `analysis/operation-inventory.csv`, never from memory** (invariant 11).
- **ADR-001** (do not build what cannot be tested) and **ADR-002** (build from scratch) are
  load-bearing: do not contradict them without a superseding ADR.
- **Always work inside a virtual environment.** Never `pip install` into a system Python.

## Gates: CI runs these, so run them before pushing

```bash
.venv/bin/python -m pytest -q --cov --cov-report=term-missing   # 100%, statements and branches
.venv/bin/ruff check .                        # `.`, never a directory list
.venv/bin/ruff format --check src tests       # scripts/ is exempt from format, not from lint
.venv/bin/mypy                                # strict, over src
python3 scripts/check_public_safe.py          # the publication gate; run before every push
```

- **Lint the whole tree.** A directory list once left `experiments/` unlinted (see the comment in
  `.github/workflows/tests.yml`). Anything to skip is an exclusion in `pyproject.toml`'s
  `[tool.ruff.lint.per-file-ignores]`, where it is reviewable.
- **Coverage is 100%, not 90%:** a gate below the measured state cannot fail. `# pragma: no cover`
  is the one hatch, and using it is a decision to write down. Scope and threshold live in
  `pyproject.toml`'s `[tool.coverage.*]`, never on a command line (#66). CI enforces it on ubuntu;
  on Windows 100% is reachable only as the union of both platforms ([FRICTION.md](FRICTION.md) F5).
- **`FakeBackend` is a first-class `Backend`, not a mock.** `tests/test_backend.py` keeps it from
  drifting from `ApiBackend` by comparing the **union** of public methods across the Protocol and
  both implementations.
- **Every guard needs an independent second source; name it.** A guard comparing a source against
  itself cannot fail, and four here did.
- **Mutation-test every guard once:** break what it protects, watch it fail, restore.
- **A test that passes for the wrong reason is worse than no test** (`TODO.md` C1).
- **Where you find a guard, test the routes that do not go through it.** Three verification passes
  checked `__setattr__` for immutability while `__init__` re-invocation re-wrote the same state.
- **Zero defect** ([ZERO-DEFECT.md](https://github.com/CloudSecurityAlliance-Internal/CINO-Engineering/blob/main/ZERO-DEFECT.md)):
  ZD-2, a 200 that looks wrong is an error; ZD-17, silence is not health. Ask of any branch that
  concludes there is no work: *if this fired forever, would anyone notice?*

## Credentials, the public line, and customer data

- **The environment carries no credential.** Tokens live in the one token file
  ([ADR-009](DECISIONS-ADR/ADR-009.md)), written once per operator by `csa-zendesk auth login`
  (also `auth whoami|status|logout`; `src/csa_zendesk/cli.py`).
- **OAuth only; no API-token path and no fallback anywhere** ([ADR-015](DECISIONS-ADR/ADR-015.md)).
  `./.env` is not a credential source. The scripts (`zd.py`, `ui_actions.py`, `probe_families.py`,
  `probe_access.py`) use the same token file and `CSA_ZENDESK_SUBDOMAIN` via `zd.authorize()`. If
  you reach for `CINO_CSA_ZENDESK`, stop: that is the deleted model. An old API token in an
  operator's local `./.env` is read by nothing here, and removing it is the operator's call.
- **Facts about Zendesk are public; facts about a tenant are not**, nor are facts about CSA's
  internal infrastructure (`scripts/check_public_safe.py`'s docstring). The line is whose facts
  they are, not "is it a credential".
- **A survey names no individuals:** `analysis/PRIOR-ART.md` aliases its servers A–L.
- **`scripts/check_public_safe.py` enforces both.** Structural patterns live in the script; literal
  terms live in gitignored `tenant-config/private-terms.txt`, because **the denylist is the
  disclosure** (FRICTION F3). `STRUCTURAL ONLY` means the private list was absent, not a clean
  bill. `.githooks/pre-commit` runs it; enable with `git config core.hooksPath .githooks`.
  **Mutation-test it after changing its patterns.**
- **Never commit** `.env`, the tenant-config extract, probe output containing rows, or clones of
  other people's repositories. `.gitignore` covers the known shapes; the judgement calls are yours.
- **Ticket bodies are customer correspondence** (names, addresses, whatever a requester pasted),
  and on the reference tenant can be audit evidence. **Probe artifacts record counts and shapes,
  never rows.**
- **Prompt injection through ticket content is the named primary risk:** a ticket body is written
  by a stranger with an interest in the outcome ([SECURITY.md](SECURITY.md)).

## Invariants that fail silently: check these when editing

Each returns a success or a plausible answer while being wrong. Evidence:
`analysis/API-SURFACE.md` §5. **Code and tests cite these numbers: never renumber** (there is
no 12).

1. **`users/me.json` answers 200 with an "Anonymous user" object when unauthenticated.** Validate
   credentials against a resource endpoint and assert a non-null `user.id`; never `users/me`.
2. **Search reports a `count` far larger than it returns:** offset only, refused past 1000
   results. Never surface `count` as pageable; `search/export` is the cursor alternative.
3. **Never emit both pagination styles on one request.** With a cursor page parameter,
   `sort_by`/`sort_order` are silently discarded (200, default order). Refuse; don't trust the
   caller.
4. **Cursor paging sorts only by `updated_at`, `id`, `status`.** `created_at` is not
   cursor-sortable; "newest" is `sort=-id`, and translating it is the tool layer's job.
5. **Offset paging dies at 10,000 records** with a typed `InvalidPaginationDepth`.
6. **Errors arrive in four incompatible envelopes.** On a validation refusal `details` is a map
   from field name to a list of problems (`base` for whole-record ones). **Iterate the keys of
   `details`; never index one**, and read it before `error`/`description`.
7. **`404` means two things:** `InvalidEndpoint` is "not on this account", `RecordNotFound` is
   "wrong id". A `403` is a plan boundary, not an outage.
8. **`503` is retryable** alongside `429`. Honour `Retry-After`; default to 10s when absent.
9. **Rate limits are layered:** an account limit plus far tighter per-endpoint buckets
   (incremental exports). Watch both header families, not just the account one.
10. **Pagination cannot be generated from the specs** (Help Center declares it on 0 of 96 paginating
    GETs). Detect it from the response, `meta` + `links`; never read silence as "unpaginated".
11. **A 404 from a guessed path is not evidence of anything.** Generate probes from
    `analysis/operation-inventory.csv` (§3, "Correction").
13. **Never omit `comment.public`.** It inherits from the ticket's first comment, so on an
    email-originated ticket it is **public** and irreversibly emails the requester and every
    collaborator. Require it explicitly, or send `false` and make publishing opt-in.
14. **`status: "closed"` is accepted and terminal.** A closed ticket refuses every further write,
    including reopening, and the web UI does not offer it. Gate it separately from both writes and
    `solve`; its tool description must say it cannot be undone.

## Design rules: read the ADR before changing its area

- **The seam returns raw envelopes.** `Backend` hands back upstream JSON unshaped; the MCP tool
  layer makes `TypedDict`s. That is why one `PolicyBackend` wrapper gates every method, and why
  ADR-002 rejects every object-mapping library.
- **Enforcement wraps the seam, not the tools,** so a library embedder gets the same guarantee as
  an MCP client. **The gate table fails closed:** a `Backend` method with no declared capability
  is refused, not delegated.
- **Tool names are bare `verb_noun`**, never `zendesk_`-prefixed. **Six names are fixed by the
  field:** `get_ticket`, `create_ticket`, `update_ticket`, `get_user`, `get_organization`,
  `get_ticket_comments`.
- **A tool is (operation × constrained arguments), bucket-pure**
  ([ADR-016](DECISIONS-ADR/ADR-016.md)): `update_ticket` refuses `comment` and `status`;
  `add_internal_note` forces `public` false; `reply_publicly` forces it true; `solve_ticket` sets
  only the status. Tools are atomic, with no composites; sequencing belongs to a workflow plugin.
  The server states what is *true*; a plugin states what to *do*.
- **Authority is ordered by reversibility** ([ADR-003](DECISIONS-ADR/ADR-003.md)): `ticket.read` <
  `ticket.note` < `ticket.write` < `ticket.reply` < `ticket.solve` < `ticket.close` <
  `ticket.merge`. The default profile is read, note, write. Reply, solve, close and merge are
  opt-in, and **no profile grants close or merge**. Solving is the on-ramp to terminal: a tenant
  automation closes solved tickets. `ticket.reply` and `ticket.merge` are `REACH_CAPABILITIES`
  and also need `CSA_ZD_ALLOW_REACH`. `ticket.attach` (`upload_file`, `delete_upload`) is off the
  chain and, like `search_tickets`, has no `subject_var`. Reasoning: `src/csa_zendesk/policy.py`.
- **Surface and authority are separate** ([ADR-006](DECISIONS-ADR/ADR-006.md)). Toolsets
  (`context`, `tickets`, `help_center`, `people`, `queues`, `reporting`, `export`, `admin`;
  default `context` + `tickets`) decide whether a tool exists; capabilities decide whether it may
  act. A registered tool without its capability is visible and refuses, a better failure than an
  absent one. **The exception is reach:** `server.py`'s `REACH_TOOLS` (`reply_publicly`) are not
  registered at all unless `CSA_ZD_ALLOW_REACH=true`. **`context` is always registered:** required
  fields vary per form. Instructions are per-toolset and compose. **Nothing enables a toolset at
  runtime.**
- **The escape hatch** ([ADR-008](DECISIONS-ADR/ADR-008.md)): `zendesk_read` (GET,
  `read_only_hint`) and `zendesk_request` (any verb, `destructive_hint`), off by default
  (`raw.read`/`raw.write`, granted by no profile). **Both refuse any path a curated tool covers**,
  naming that tool. Every refusal names its remedy; refusals are logged by method and path, never
  the body. It skips nothing else: injection wrapping, the pagination refusal, rate limiting, the
  error taxonomy.
- **Async** ([ADR-007](DECISIONS-ADR/ADR-007.md)): a bulk tool submits, polls briefly, and returns
  the outcome or the job id (`get_job_status` reads it later); at most 30 jobs account-wide before
  `TooManyJobs`. **Always report per-record outcomes**: a half-succeeded job never reads as
  success. Our own aggregation has no handles.
- **Bulk reads aggregate and never persist** ([ADR-005](DECISIONS-ADR/ADR-005.md)): `summarise_*`
  folds pages and discards them, so rows never reach the model or disk; `export_*` writes a file
  for a person to an operator-configured directory, and the model does not query it: **there is no
  query tool over exports.** **Nothing is cached** except the OAuth token file (ADR-009). **Scope
  is mandatory**, and an answer **reports how much it sampled**.
- **Three testing tiers** ([ADR-004](DECISIONS-ADR/ADR-004.md)): unit (every path, `FakeBackend`,
  offline, gates CI); integration (reads and reversible writes, live, opt-in); observational
  (irreversible operations, verified read-only from the platform's own instances). No observable
  instances and no safe test means excluded under ADR-001. Observational reports *insufficient
  evidence* distinctly from *pass*.

## Traps: MCP SDK and Python (all verified)

The MCP SDK, against `mcp` 2.1.0:
- Nothing may touch **stdout** under stdio.
- Raise the SDK's **`ToolError`**, or your message is discarded.
- **Never `Field(alias=…)`** on a tool parameter.
- **`mcp.server.fastmcp` does not exist:** it is `from mcp.server import MCPServer`.
- Sync handlers run on **worker threads**, so a non-thread-safe client must be thread-local.
- **`TypedDict` from `typing_extensions`** below Python 3.12.
- Do not block `initialize` on a network call.

Python; each was written into a fix here and was wrong:
- **`inspect.signature(cls)` raises `ValueError`** on an `Exception` subclass with no `__init__` of
  its own, so skip those. `'__init__' in vars(Exception)` is `True`, yet
  `Exception.__init__ is BaseException.__init__` is `False`.
- **`raise X from None` does not sever chaining:** `__context__` still holds the original. To
  detach, raise after the `except` block exits. An httpx exception's `.request.headers` holds the
  `Authorization` header.
- **`vars(obj)` raises `TypeError` on a `__slots__` class**; use `hasattr` for a construct-once
  sentinel.
- **Since 3.12, `isinstance` against a `runtime_checkable` protocol uses
  `inspect.getattr_static`**, ignoring `__getattr__`. Materialise the methods onto the class if
  protocol identity matters.
- **`from __future__ import annotations` binds `annotations`** in the module namespace, which leaks
  into `dir()`-based public-surface checks.
- **`base64` is not obfuscation.** A leak check on plaintext and `repr()` passes while
  `vars(client)` hands over the secret one `b64decode` away, and a redacted `__repr__` hides it.

## Commands

```bash
export CSA_ZENDESK_SUBDOMAIN=<subdomain>               # no default, deliberately
export CSA_ZENDESK_MCP_SERVER_IDENTIFIER=<client-id>   # the OAuth client's Identifier
# optional: CSA_ZENDESK_SCOPES, space-separated, defaults to `read`

python3 scripts/inventory.py           # regenerate the operation inventory from specs/
python3 scripts/probe_access.py        # what this credential reaches, GET only
python3 scripts/survey_tools.py        # re-run the ecosystem survey from the clones
python3 scripts/extract_config.py      # tenant configuration -> gitignored tenant-config/
```

## Where things live

- **`TODO.md`:** the index of all open work; with open Issues, that is everything. Its
  **consideration pile** is what was deliberately not committed to: read it before proposing a
  "missing" feature.
- **`analysis/API-SURFACE.md`:** start here. The API, what this credential reaches, and the
  probe-verified behaviours (§5).
- **`analysis/UI-ACTION-MAP.md`:** the tool surface derived from the agent web interface.
- **`analysis/PRIOR-ART.md`:** the ecosystem survey, anonymised on purpose.
- **`analysis/OFFICIAL-CLIENTS.md`:** what Zendesk's Ruby and PHP clients know; §7 is what we take.
- **`analysis/CLIENT-LIBRARIES.md`:** the build-vs-adopt assessment.
- **`analysis/operation-inventory.csv`:** the authoritative path list.
- **`specs/`:** upstream OpenAPI snapshots with provenance and sha256s; someone else's moving
  target.
- **`scripts/`:** everything in `analysis/` is regenerable from here; nothing there is
  hand-maintained.
- **`experiments/`:** runnable probes, each with a dated `RESULTS.md`.
- **`DECISIONS-ADR.md`** + `DECISIONS-ADR/`; **`WAITING-FOR.md`** + `WAITING-FOR/` (conditions with
  observable triggers); **`SECURITY.md`**; **`FRICTION.md`**; **`RELEASING.md`**.
- **Siblings sharing this architecture:**
  [`csa-google-workspace`](https://github.com/CloudSecurityAlliance/csa-google-workspace) and
  [`csa-skilljar`](https://github.com/CloudSecurityAlliance/csa-skilljar).
