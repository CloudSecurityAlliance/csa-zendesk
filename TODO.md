# TODO

The index of **all** open work on this project, per the CINO todo-index convention.
Sweeping this file plus open GitHub Issues finds everything; nothing else needs searching.

Ordered roughly by leverage-to-effort within each section. The **consideration pile** at the
bottom holds things deliberately *not* committed to — check it before proposing a "missing"
feature.

Status: `open` · `in progress` · `blocked` · `done`

---

## A. Research deferred

| | Item | Status | Notes |
|---|---|---|---|
| A1 | **Reconcile our scope against what the official SDKs cover.** Their union is 97 resources; ours is 125 families. Work out which of their families we lack, whether each is reachable here, and whether it belongs in scope. | open | Explicitly deferred by ADR-002. The awkward cases are families they model that we cannot test. |
| A2 | **Resolve the six ambiguous Help Center 404s** — Badges, Badge Categories, Badge Assignments, Guide Media Objects, Account Custom Claims, Help Center JWTs. Each returned a bare 404 from an *inferred* path, so "not on this plan" and "wrong path" are indistinguishable. | open | Needs the reference page per family, not another guess. See `API-SURFACE.md` §4b. |
| A3 | **Establish the `guide/search` filter contract.** It 400s without a `filter` object, then again without `filter[locales]`. The older `help_center/articles/search.json` works and is the one to start from. | open | |
| A4 | **Evaluate `prompt-security-utils`** as a dependency versus implementing the injection-wrapping pattern directly. | open | Prompt injection through ticket bodies is the primary risk; nothing in the field addresses it except one server. |
| A5 | **Mine the Python libraries** (`zenpy`, `python-zendesk`) the way the official ones were mined. | open | Expected to be thin — zenpy's two best ideas (rate-limit budget, a typed error for the search ceiling) are already captured. |
| A6 | **Decide whether Help Center localisation is real work here.** Translations are 17 operations and one surveyed server built a whole workflow on them. If nobody localises, deprioritise deliberately rather than by omission. | open | Needs a human answer, not a probe. |

## B. Design still owed

| | Item | Status | Notes |
|---|---|---|---|
| B1 | ~~Architecture options~~ | **done** | Settled across ADR-002/003/005/006/007/008 and assembled in `docs/superpowers/specs/2026-09-01-csa-zendesk-design.md`. |
| B2 | ~~Implementation plan for Block 0~~ | **done** | `docs/superpowers/plans/2026-09-08-block-0-foundations.md` — 8 tasks, 46 steps, TDD throughout. |
| B12 | ~~Whole-project sequence~~ | **done** | `docs/superpowers/specs/2026-09-17-csa-zendesk-whole-project-design.md` — build order vs enable order, the third control (allowlists), hatch-first delivery, and bucket-pure tool boundaries. |
| B13 | **ADR-011 — allowlists select subject**, and the allowlist is a blast-radius control rather than a security boundary. | open | Owed by the whole-project design. Adds a third axis to ADR-006. |
| B14 | **ADR-012 — the hatch is the primary phase-one surface.** | open | Owed by the whole-project design. Changes ADR-008's posture; must reconcile with GOALS failure mode #2. |
| B15 | **ADR-013 — tool boundaries never span an impact bucket.** | open | Owed by the whole-project design. Likely an extension of ADR-010. |
| B11 | ~~Block 0b plan~~ | **done** | `docs/superpowers/plans/2026-09-17-block-0b-oauth.md` — 9 tasks, TDD throughout. |
| B20 | **The token file is written atomically but without a lock.** ADR-009 specifies "atomically … under a lock file". `os.replace` is atomic on POSIX so concurrent writers cannot tear the file; the residual risk is two overlapping refreshes losing one rotated refresh token, which costs a re-login rather than corruption. | open | Deliberately deferred by the Block 0b plan's self-review — a correct cross-platform lock is more code than the rest of the store, for a recoverable failure. Revisit when the server genuinely runs under more than one MCP client. |
| D12 | **`check_public_safe.py` matches denylist terms as bare substrings**, so a short private term that happens to sit inside an ordinary English word refuses the commit. Hit twice on 2026-09-17 by one four-letter term inside a common gerund — and then again when this very entry tried to quote the example, which is the tell: a gate you cannot describe a false positive in. | open | Word-boundary matching is the obvious fix and is *not* obviously safe — a stricter matcher risks false **negatives**, which for a publication gate is the worse direction, and several real terms contain hyphens and dots that word boundaries treat inconsistently. Deserves its own considered change with tests, not a drive-by. Same defect class as a leak-test canary short enough to occur by chance. |
| B3 | ~~`CLAUDE.md`~~ | **done** | Fourteen invariants, the public/private line, testing tiers, and the surface/authority split. |
| B4 | ~~Decide the bulk / async story~~ | **done** | ADR-007: surface Zendesk's jobs with brief polling then a handle; bound our own aggregation instead of deferring it; hold no job state. |
| B5 | ~~Decide on the store-and-query pattern~~ | **done** | ADR-005: aggregate in memory for the model, export to disk for people, persist neither. Store-and-query rejected — it reverses `SECURITY.md`'s no-persistence position and its benefit is covered twice over. |
| B6 | ~~Decide the escape-hatch question~~ | **done** | ADR-008: `zendesk_read` and `zendesk_request`, both off by default, both refusing any path a curated tool covers, every refusal naming its own remedy. |
| B8 | **A queryable mirror of Zendesk data in a database** — post-1.0 and large. Would make analytical questions instant and bulk operations straightforward, instead of streaming pages under a rate limit. Requires: an incremental-export sync pipeline, a schema, staleness handling, PII retention decisions, and access control — and it *is* persistence at scale, so it needs its own ADR superseding ADR-005's position rather than an extension of it. **Check first whether Customer360's existing Zendesk mirror can be queried instead of building a second one**; duplicating a mirror that already exists is the expensive mistake here. | open | Not 1.0. Recorded so the aggregation design in ADR-005 is understood as the answer *for now*, not the permanent one. |
| B7 | ~~Decide the toolset model~~ | **done** | ADR-006: eight toolsets, default `context` + `tickets`, `context` always on, per-toolset composing instructions, no runtime enabling. |

## C. Verification gaps

| | Item | Status | Notes |
|---|---|---|---|
| B9 | ~~Should `admin.write` split per object type?~~ | **answered** | The 2026-09-17 design answers it with `CSA_ZD_ALLOWLIST_ADMIN` — per-object scope is finer and more honest than splitting the capability 225 ways. The revisit trigger fired: the configuration-improvement loop means `admin` *will* be granted routinely. |
| B10 | **Should `bulk` be complemented by a magnitude threshold** above which an operation needs confirmation? One surveyed server does this. `bulk` gates authority; a threshold gates scale. | open | Not the same question. |
| C1 | **The requirements model over-reports on conditional forms.** It matched a live 422 exactly — but on a form with *zero* conditional rules, so the match validated the easy half. On a conditional form it lists mutually exclusive branches as both required. | open | Needs a ticket on a conditional form. `experiments/solve-required-fields/RESULTS.md`. |
| C2 | **`required_on_statuses.type` is not enumerated.** `SOME_STATUSES` observed; `ALL_STATUSES` inferred and unverified. The whole `agent_conditions` structure is undocumented. | open | Keep the compute-vs-422 comparison as a conformance test so upstream shape changes fail loudly. |
| C3 | ~~Confirm `status: "closed"` is rejected~~ | **done** | It is **accepted**, and terminal. See `API-SURFACE.md` §5.4d. The earlier claim that it was automation-only was wrong. |
| C4 | ~~Confirm the default for `comment.public` when omitted~~ | **done** | There is no fixed default: it **inherits from the ticket's first comment**, so email-originated tickets default to public. `API-SURFACE.md` §5.4f. Answered from docs + read-only observation rather than by emailing four uninvolved people. |
| C6 | **`merge_tickets` is a closing operation** — *placed automatically by the classification pass under bucket purity; keep as a conformance check rather than a manual fix.* — merging closes the source ticket, which observational sampling suggests is a significant real-world path to the irreversible state. It needs a gate at `ticket.close` level, not `ticket.write`, and a tool description that says so. | open | Found by ADR-004's technique, not by reading docs. |
| C5 | **Seed a cursor-capability table** from the Ruby client's path list, then verify each against live probes. Single-sourced today. | open | |

## D. Repo and process

| | Item | Status | Notes |
|---|---|---|---|
| D1 | **Create the public GitHub repo** once there is working code, and push. | done | Repo exists; Block 0 is the first working code. |
| D2 | **Set the Airtable file-registry URLs** — README, DECISIONS-ADR, WAITING-FOR, TODO, and the rest. | blocked | On D1; the URLs would 404 today. |
| D3 | **Decide whether `SECURITY-RESOURCES.md` is owed.** This project has no external surface of its own but handles an admin credential and untrusted ticket text. | open | |
| D4 | **Add the definitions endpoints to the Zendesk config backup.** Four endpoints; the backup covers 19 config objects and not these. | blocked | Parked deliberately — that repo is politically sensitive. |
| D6 | **The CI publication gate runs with structural patterns only.** `tenant-config/private-terms.txt` is gitignored, so CI cannot check literal tenant terms — it reports `STRUCTURAL ONLY`, which is the designed behaviour, but the literal tier is enforced only by the local pre-commit hook. Decide whether to ship the term list as a CI secret or accept the split. | open | Found while writing the Block 0 plan. |
| D7 | **`tenant-config/private-terms.txt` has no reconstitution path.** It is gitignored by design (the denylist is the disclosure), so a fresh clone silently loses the literal tier. Document how it is rebuilt, or where the canonical copy lives. | open | |
| D5 | Run `scripts/check_upstream`-equivalent periodically: re-fetch the specs, re-run `inventory.py` and `probe_access.py`, diff. | open | No `OPERATIONAL-RESOURCES.md` yet; create one if this becomes recurring. |

## E. Block 0 debts — owed before or during Block 1

Every one of these is a thing Block 0 got away with because it has **one** operation. Each
becomes wrong, misleading, or invisible at fifty.

| | Item | Status | Notes |
|---|---|---|---|
| E1 | **Read the capability configuration.** Refusals used to name `CSA_ZENDESK_PROFILE` and `CSA_ZENDESK_CAPABILITIES`; no code reads either, so the messages were rephrased generically. The server entry point must actually read config, and the refusal text should name the real variables once they work. | open | "Every refusal names its own remedy" is false until this lands. |
| E2 | **Defend stdout at the server entry point.** The package never writes to stdout (enforced by ruff T20 plus an import-time guard), but an *embedder* calling `logging.basicConfig(stream=sys.stdout)` reroutes this library's records onto the MCP protocol channel and corrupts the session. The entry point should install a stderr handler on the package logger and/or refuse to start if a stdout handler is attached. | open | Not fixable inside the library — it cannot see the embedder's config. |
| E3 | **`BULK` is declared and enforced nowhere.** ADR-003 makes it a cross-cutting additive capability, but no gate composes it and there is no helper. Add the composition (`bulk_of(base)` or similar) while there are **zero** call sites to migrate. | open | Cheapest now, most expensive after Block 1's bulk tools. |
| E4 | **A `None` gate means "ungated" and nothing enumerates the set.** With one entry that is readable; with fifty, an accidentally-ungated read is invisible. Make the ungated set explicit and assert it. | open | |
| E5 | **No rate-limit accounting at all** (invariant 9). Retries honour `Retry-After` and a cumulative budget, but nothing tracks the account limit or the much tighter per-endpoint buckets. Incremental export is an order of magnitude stricter. | open | Bites hardest on the export and reporting toolsets. |
| E6 | **`ZendeskClient` will need ~50 `cast(Envelope, ...)` calls.** `PolicyBackend`'s methods are materialised with `setattr`, so mypy sees `Any` through `__getattr__`. Ship a `.pyi`, or give the wrapper an explicitly-typed dispatch handle, before the cast count grows. | open | Decide before the second operation, not the fiftieth. |
| E7 | **`ZendeskClient(bare_backend)` is legal and ungated.** Correct for a library embedder who opted out; wrong for the MCP server, which must never construct one. The entry point needs the assertion the library deliberately does not make. | open | |
| E8 | **`_http.py` is approaching ADR-002's 400-line tripwire** with three known additions owed (rate limiting, OAuth refresh, pagination following). Plan the split rather than discovering it. | open | |
| E9 | **An unpickled `PolicyBackend` refuses with a typed error, but pickling one is meaningless.** Consider a `__reduce__` that refuses outright — a security wrapper arguably should not be picklable. | open | Fails closed today; cosmetic. |
| E10 | **`test_capability_constants_and_the_all_tuple_agree` filters by upper-case name** and will misfire on the first non-capability upper-case constant added to `policy.py`. | open | Well-commented, but fragile by construction. |

---

## Consideration pile — deliberately not committed to

Recorded so they are not re-proposed as oversights.

- **The six plan-gated families** (IT Asset Management, Group SLA Policies, Workspaces, Ticket
  Form Statuses, Audit Logs, HC Service Catalog Items). Excluded by ADR-001; reopening
  conditions in WAITING-FOR-001. **Audit Logs is the one worth revisiting** if a need appears.
- **Voice / Talk** — 60 operations with a published spec, deliberately post-1.0.
- **Live Chat, Messaging, AI Agents, Sales CRM** — no published spec, post-1.0 or out of scope.
- **Caching.** Both sibling projects deliberately have none; `zenpy` caches by default and that
  is a reason not to build on it, not a feature to copy.
- **An ORM / object mapper.** ADR-002. The seam returns raw envelopes on purpose.
- **A local attachment cache.** One surveyed server has nine tools for it. A local cache of
  customer attachments is a data-retention decision, not a convenience.
- **Naming with a `zendesk_` prefix.** Settled: bare `verb_noun`. The client namespaces already.
