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
| B1 | **Architecture options, with trade-offs and a recommendation.** The live question is how ~700 reachable in-scope operations map onto a tool surface a model can choose from. | open | The blocker for everything else. |
| B2 | **Design spec**, then an implementation plan. | blocked | On B1. |
| B3 | **`CLAUDE.md`** — the behavioural contract. The probe-verified invariants are the "fails silently" section and will otherwise be re-derived the hard way. | open | High leverage, low effort. |
| B4 | **Decide the bulk / async story.** Job statuses are modelled by exactly one client anywhere, as a bare resource with no polling or partial-failure handling. | open | One of the four real gaps. |
| B5 | **Decide on the store-and-query pattern** — writing large responses to disk and querying them out of band, rather than through the model's context. | open | Dissolves the tension between full coverage and small context. |
| B6 | **Decide the escape-hatch question** — whether to ship a policy-gated generic request tool, and if so how narrowly gated. | open | |

## C. Verification gaps

| | Item | Status | Notes |
|---|---|---|---|
| C1 | **The requirements model over-reports on conditional forms.** It matched a live 422 exactly — but on a form with *zero* conditional rules, so the match validated the easy half. On a conditional form it lists mutually exclusive branches as both required. | open | Needs a ticket on a conditional form. `experiments/solve-required-fields/RESULTS.md`. |
| C2 | **`required_on_statuses.type` is not enumerated.** `SOME_STATUSES` observed; `ALL_STATUSES` inferred and unverified. The whole `agent_conditions` structure is undocumented. | open | Keep the compute-vs-422 comparison as a conformance test so upstream shape changes fail loudly. |
| C3 | **Confirm `status: "closed"` is rejected** rather than silently accepted. | open | Cheap; do it on the test ticket. |
| C4 | **Confirm the default for `comment.public` when omitted.** The failure is irreversible and lands in a requester's inbox. | open | The most consequential unknown on this list. |
| C5 | **Seed a cursor-capability table** from the Ruby client's path list, then verify each against live probes. Single-sourced today. | open | |

## D. Repo and process

| | Item | Status | Notes |
|---|---|---|---|
| D1 | **Create the public GitHub repo** once there is working code, and push. | blocked | On having something to show. |
| D2 | **Set the Airtable file-registry URLs** — README, DECISIONS-ADR, WAITING-FOR, TODO, and the rest. | blocked | On D1; the URLs would 404 today. |
| D3 | **Decide whether `SECURITY-RESOURCES.md` is owed.** This project has no external surface of its own but handles an admin credential and untrusted ticket text. | open | |
| D4 | **Add the definitions endpoints to the Zendesk config backup.** Four endpoints; the backup covers 19 config objects and not these. | blocked | Parked deliberately — that repo is politically sensitive. |
| D5 | Run `scripts/check_upstream`-equivalent periodically: re-fetch the specs, re-run `inventory.py` and `probe_access.py`, diff. | open | No `OPERATIONAL-RESOURCES.md` yet; create one if this becomes recurring. |

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
