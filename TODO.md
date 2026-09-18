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
| A1 | **Reconcile our scope against what the official SDKs cover.** Their union is 97 resources; ours is 125 families. Work out which of their families we lack, whether each is reachable here, and whether it belongs in scope. | open | Explicitly deferred by ADR-002. The awkward cases are families they model that we cannot test. **Now an input to the scoping triage (B16) rather than standalone.** |
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
| B19 | **Negative-space research** — what a human can do in the Zendesk web interface that no API operation reaches. Sources are complaints (vendor forums, Stack Overflow, GitHub issues on client libraries), third-party tools that say "we cannot sync X", the vendor's own known-limitations pages, and evidence of an internal API the web app uses. | open | Added 2026-09-17, §3b of the whole-project design. The triage partitions the *API* surface; a UI-only capability is invisible to it. `UI-ACTION-MAP.md` does not cover this — it asks the API what the UI can do, so it cannot find absences. Each gap decides: web automation *with* a drift watcher, out-of-scope with a reopening condition, or WAITING-FOR. |
| B16 | **Scoping triage (B0c)** | **first pass done** | `analysis/SCOPING-TRIAGE.md` + `scripts/triage.py`. 335 now / 387 later / 48 never / 52 blocked, from 822. Still owed: the official-server input (#17) and the SDK reconciliation (A1), both of which can only *move* families, not invalidate the pass. |
| B18 | **Correct `operation-classification.csv` before B1 derives anything from it.** Three defects found by the triage: 15 of 17 `outward_facing=yes` rows are reads; four reads are flagged irreversible; and the public reply — the operation the whole design is built around — is a *parameter on ticket update*, so it is not in the table at all. | open | Blocks B1, which derives both the capability set and the tool list from this table. DEC-015 anticipated the third case ("reach may be a property of the call rather than the tool"); ADR-003 resolves it at the tool layer; the table has no way to express it. |
| B13 | **ADR-011 — allowlists select subject**, and the allowlist is a blast-radius control rather than a security boundary. | open | Owed by the whole-project design. Adds a third axis to ADR-006. |
| B14 | **ADR-012 — the hatch is the primary phase-one surface.** | open | Owed by the whole-project design. Changes ADR-008's posture; must reconcile with GOALS failure mode #2. |
| B15 | **ADR-013 — tool boundaries never span an impact bucket.** | open | Owed by the whole-project design. Likely an extension of ADR-010. |
| B17 | **ADR-014 — scope is admitted per family, and some operations are refused outright.** | open | Formalises the scoping triage. Narrows ADR-001 from "cannot test" to one of four reasons not to build. |
| B23 | ~~Block 0c plan — the tool-surface validation slice~~ | **done** | `docs/superpowers/plans/2026-09-17-block-0c-tool-slice.md` — 6 tasks. Runs **before** Block 0b: it needs no network and it tests the design that 0b's work would otherwise be built on. |
| B21 | **Write the example workflow plugin** — the simple default that ships in this repo, showing how atomic tools are sequenced (ADR-016). Must carry the *reach last* rule: order irreversible actions after recoverable ones, so a failure costs a retry rather than an email already sent. | open | This is also rung E2's deliverable — the capture artifact the whole loop produces. CSA's own procedures become separate plugins; see PLUGIN-DISTRIBUTION.md for which marketplace. |
| B25 | **Nothing asserts `PROFILES` is a subset of `ALL_CAPABILITIES`.** Profile entries are hand-written literal sets; `test_capability_constants_and_the_all_tuple_agree` checks the *constants* against the tuple, not the *profiles* against it. A typo in a profile grants a capability no gate requires, and no test notices. | open | Surfaced by the Task 2 review while judging a narrower finding. Currently true, unasserted — the whole point of a named profile is that "nobody composes a capability list correctly under time pressure", and this is the check that makes that claim hold. One assertion. |
| B24 | **The bucket-purity checker is blind to an operation that backs exactly one tool.** `scripts/check_boundaries.py` detects impurity only by comparing siblings on a shared operation, so a lone tool gets no check at all — five of the slice's six operations are in that position. `create_ticket` needed a constraint for precisely the reason `PUT /tickets/{id}` did, and only a human caught it. | open | **This generalises badly to 822 operations, where a single-tool operation is exactly the one nobody thought to split.** A real fix cross-references the OpenAPI request schema in `specs/` for impact-bearing fields (`comment.public`, `status`, anything that changes reach or reversibility) and demands a constraint wherever one appears. Must land before the full derivation, not after. |
| B22 | **`policy.py`'s callable `Gate` may now have no user.** It exists for composite tools, which ADR-016 rules out. Do not remove it until the tool surface confirms nothing needs a capability set computed from arguments. | open | YAGNI cuts both ways — it was written for `update_ticket` composites that will not be built, but a later admin or bulk tool may still need it. |
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

## F. Live end-to-end testing

| | Item | Status | Notes |
|---|---|---|---|
| F1 | **A live end-to-end run against a real ticket, created by emailing support@.** File a ticket by email, then walk it through the whole surface — read it, assign it, add an internal note, reply publicly, solve it, close it — and record what the API actually did at each step in a dated `experiments/*/RESULTS.md`. | open | **Blocked on Block 0b**: ADR-015 removed the API-token path from the library, so nothing can reach Zendesk until OAuth exists. Not blocked on 0c, which is deliberately offline. |
| F2 | **An email-originated ticket is the right fixture *and* the sharpest one.** Its first comment is public, and `comment.public` has no fixed default — it **inherits from the ticket's first comment** (invariant 13, C4). So on this fixture every comment defaults to **public** unless forced otherwise, which is exactly the trap `add_internal_note` forcing `public=false` exists to close. A fixture that defaults to safe would not test the control. | open | Pairs with F1. The requester is whoever sent the email, so a public reply emails **that** account — if it is a test account, the blast radius of the whole exercise is one inbox we control. |
| F3 | **Register the test tickets in `CSA_ZD_ALLOWLIST_WRITE` by hand.** A ticket created by email is not tool-created, so it carries no `csa-zendesk-created` tag and provenance scope does not cover it. This is the operator-granted half of the allowlist doing its job, and F1 is its first real exercise. | open | Also the first live check that the read/write asymmetry holds: `READ=*` so triage sees the queue, `WRITE=` the test ids only. |
| F4 | **Adopt the fleet's demo-as-end-to-end-test pattern** rather than inventing a Zendesk-specific harness. `csa-google-workspace` proved it once — one artifact that is simultaneously the demo, the release smoke test, the tool-description quality check and the feedback collection point; 3 live runs found 4 real bugs, one of which had survived 660 green unit tests. | open | CINO tracks this as needing a **second** adopter before it becomes fleet standard, and says adopting it unblocks the `mcp-server-development` skill. `csa-zendesk` is the natural one: see [`research/mcp-servers/DEMO-AS-END-TO-END-TEST.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/research/mcp-servers/DEMO-AS-END-TO-END-TEST.md). |
| F5 | **The e2e run is how each enablement rung is earned.** E1 read → E2 write → E3 admin read → E4 admin write → E5 reach. A rung is earned by the previous one working against a real ticket, not by elapsed time. | open | Makes the ladder in the whole-project design testable rather than declarative. |

---

## D. Repo and process

| | Item | Status | Notes |
|---|---|---|---|
| D8 | ~~Mint API tokens before the window closes~~ | **decided — no** | [ADR-015](DECISIONS-ADR/ADR-015.md): OAuth only, no further tokens, the 2026-10-27 window is allowed to close. A stockpile buys until 2027-04-30 regardless and argues against building OAuth this quarter. |
| D10 | **Remove the API token code path** from `README.md` and `CLAUDE.md` as a supported configuration, per ADR-015. The research scripts under `scripts/` keep theirs — development tooling, not product. | open | Low effort; do it before anyone writes auth code against the old assumption. |
| D11 | **Port the research scripts to OAuth** — `zd.py`, `ui_actions.py`, `probe_families.py`. | blocked | On Block 0b. They inherit the 2027-04-30 expiry; nothing at runtime depends on them, so this is a follow-on rather than a precondition. |
| D1 | ~~Create the public GitHub repo~~ | **done** | The repo has existed since 2026-08-31 and is public. This entry was stale; it unblocks D2. |
| D2 | **Set the Airtable file-registry URLs** — README, DECISIONS-ADR, WAITING-FOR, TODO, and the rest. | open | **Unblocked**: D1 is done, the repo is public, the URLs resolve. Also confirm the CINO Product for MCP Servers exists and holds this project alongside `csa-google-workspace` and `csa-skilljar` (asked for in the 2026-09-12 session; never confirmed). |
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

- **The Zendesk MCP server written by a CSA colleague.** Asked for during the 2026-09-12 design
  session and **deliberately dropped 2026-09-17**: the prior-art survey exists to learn from servers
  with real adoption, and this one has none. Including it because the author is internal would be
  selecting on relationship rather than on signal, and the aliasing convention exists precisely so
  the survey is about the ecosystem rather than about people. Recorded so it is not re-proposed as an
  oversight.
