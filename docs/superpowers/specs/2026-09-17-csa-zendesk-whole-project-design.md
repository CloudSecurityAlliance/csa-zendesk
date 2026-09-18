# csa-zendesk — whole-project design

**Date:** 2026-09-17
**Status:** Approved in conversation; ADRs owed (see *Decisions this creates*)
**Supersedes nothing.** Extends `2026-09-01-csa-zendesk-design.md`, which settled the architecture.
This settles the **arc** — what gets built, in what order, and what earns the right to act.

---

## The problem this spec solves

Ten ADRs are Active, the architecture is settled, and `main` carries no code. `GOALS.md` names the
risk in its own words: *"Ten ADRs and no server. The design is unusually well-settled for a project
with one backend method implemented; that asymmetry is a risk, not an achievement."*

What was missing was not more design. It was a **sequence**: Block 0 exists, "Block 0b" is named but
unplanned, and nothing after it is defined. This spec supplies the arc from here to a server doing
real work, and the rule for how authority is granted along the way.

## What the project is for

Not "an MCP server for Zendesk". The server is one component of a loop:

```
human + AI work real tickets  →  what works is captured as skills
        ↑                                      ↓
   Zendesk configuration improves  ←  skills become plugins, then an agent
```

Two things follow that a tool-list-shaped project would miss.

**The second loop is a deliverable, not a side effect.** Working the queue teaches you that Zendesk
itself is mis-configured — a ticket type recurs and wants a trigger, a macro, an automation. Acting
on that is in scope. It is also why `admin` is not a post-1.0 afterthought here, and why TODO **B9**
("should `admin.write` split per object type? Deferred because `admin` is off by default in 1.0")
has expired: `admin` will be granted routinely.

**Capture is the point of the work, not a report on it.** Each rung of the enablement ladder below
produces skills as its real output. The tickets get worked either way; the skills are what compounds.
This is `WORK-THROUGH-AI.md`'s three pillars — route, capture, improve — with support as the domain.

---

## 1. Three controls, not two

`ADR-006` establishes two orthogonal dimensions. This spec adds a third.

| Control | Question it answers | Mechanism |
|---|---|---|
| **Toolset** | does this tool *exist*? | operator config at launch (`ADR-006`) |
| **Capability** | may an existing tool *act*? | fail-closed `PolicyBackend` (`ADR-003`) |
| **Allowlist** | may it act on *this object*? | operator config; new |

All three are operator-set, all three fail closed, and all three are enforced at the same seam — so a
library embedder gets the same refusal an MCP client does.

### The allowlist is a blast-radius control, not a security boundary

**Stated plainly because it will otherwise be misread.** The server authenticates as the operating
user via OAuth (`ADR-009`). The invariant is:

> **You cannot do anything through this tool that you cannot already do in Zendesk.**

Server-side Zendesk permissions are what is actually relied upon. The allowlist protects against
**the agent doing the wrong thing** — it does not protect against a user exceeding their authority,
and for an administrator it constrains almost nothing, because an administrator can already reach
everything. That asymmetry is precisely why the configuration loop (§5, rungs E3–E4) needs care that
the ticket loop does not.

The failure this prevents is someone later granting the tool a wider credential on the theory that
"the allowlist will hold it." It will not.

### Shape, adopted from `csa-google-workspace`

That server's `allowlist.py` is the reference implementation and its design decisions transfer:

- **Configured in the environment, not a file.** The client configuration is the artifact an operator
  controls and can see; a path adds an indirection whose target changes without the config changing.
- **Three outcomes, and the third is the point.** A value is `*`, or a set, or **unusable** — and
  unusable always means *nothing permitted*, never "ignore the setting".
- **`*` is distinct from empty.** One is a deliberate, logged decision; the other is
  indistinguishable from a typo, and is therefore an error.
- **Enforced by id**, not by any string form of it.
- **Entries carry a reason and a line number.** An audit needs to know why something was permitted.
- **Near-misses are loud errors, not silently-inert entries.**

### Three settings, and the read/write asymmetry is the trick

```
CSA_ZD_ALLOWLIST_READ  = *                      # the entire live queue
CSA_ZD_ALLOWLIST_WRITE = 44821, 44822, 44823    # test tickets, with reasons
CSA_ZD_ALLOWLIST_ADMIN =                        # empty: no configuration objects
```

Triage must see everything — a read allowlist of ten tickets makes `search_tickets` useless and kills
the primary use case. Writes must touch almost nothing. Same mechanism, opposite settings, which is
why these are independent variables rather than one.

`CSA_ZD_ALLOWLIST_ADMIN` also answers **B9** without splitting the capability 225 ways: a trigger
fires on *every future ticket*, so its blast radius is unbounded even though the object is small.
Naming the specific objects that may be changed is a finer control than any capability split, and a
more honest one.

### Four Zendesk-specific rules

1. **Created tickets are in scope by provenance, not by list.** `create_ticket` cannot be checked
   against an id that does not yet exist. On creation the server sets the tag `csa-zendesk-created`,
   and scope resolves as:

   ```
   writable(ticket) = id ∈ CSA_ZD_ALLOWLIST_WRITE        # operator-granted
                    OR ticket has tag csa-zendesk-created  # self-granted, by provenance
   ```

   Nothing is persisted locally, so `ADR-005` holds; the marker survives restarts and fresh clones;
   and it is **visible in the Zendesk UI**, so a human can see which tickets the tool treats as its
   own and can revoke by removing a tag. Operator-granted and self-granted scope stay
   distinguishable in an audit, which a merged list would lose.

   *Accepted trade:* a human can widen scope by adding the tag. That is a deliberate act by someone
   who could edit the ticket directly anyway — consistent with the invariant above.

2. **The write check is on the target of the write, never on what a search returned.** Otherwise
   "I found it, therefore I may change it" leaks scope through the read path.

3. **Configuration objects are not tickets** and use the third setting. Empty by default.

4. **Allowlisting a ticket grants its comments, and nothing else.** Users and organizations are
   reachable *through* a ticket; touching a requester is a separate scope. Otherwise one test ticket
   quietly authorises writes to a real customer record.

---

## 2. Delivering full access: the hatch comes first

**Decision: full reach arrives through the generic tools, and curation follows observed use.**

`ADR-008` already specifies `zendesk_read` and `zendesk_request`, both off by default, both refusing
any path a curated tool covers, every refusal naming its own remedy. This spec promotes them from
escape valve to **the phase-one operating surface**.

**The hatch ships with B0b, not with B4.** It needs only the client, the gate and authentication — not
the curated surface — so it is the one part of the build that is deliberately delivered early. That is
what lets rung E1 begin while B1–B5 are still in progress, and it is the only place the build track
and the enablement track overlap. Everything else follows §5: build first, enable after.

That is a change of posture and it needs its own ADR, because `GOALS.md` currently lists
*"the generic request tool becomes the way things are done"* as failure mode #2.

**Why it is not that failure.** The hatch refuses what curated tools cover, so curation mechanically
shrinks the hatch rather than competing with it. The measure of the failure is therefore observable:
if hatch usage is not falling as tools land, the curation is aimed wrong.

**What the hatch buys beyond speed:** every hatch call is logged with its path, and the distribution
of those paths is evidence about which operations deserve curation — designed **once**, from data,
rather than guessed from the API surface or re-carved continuously.

---

## 3. Scoping triage — deciding what the surface *is*

**This step comes before classification, and it is opt-in rather than opt-out.**

Today the surface is opt-out: every reachable operation gets built unless some reason excludes it.
That is how 822 became the working number without anyone deciding it should be. The triage inverts
the burden of proof — each family is admitted for a recorded reason, and "why does this exist?" has
an answer per family rather than per objection.

### Four buckets

| Bucket | Meaning | Built? |
|---|---|---|
| **Now** | needed for the work the server exists to do | yes, in the one-pass build |
| **Later** | plausibly wanted, not yet justified | classified, not generated |
| **Never** | **should not be possible through this tool at all** | not generated, and recorded as refused |
| **Blocked** | wanted but untestable (`ADR-001`) or plan-gated (`WAITING-FOR-001`) | not generated; existing triggers govern |

**"Never" is the bucket that does not exist today, and it is the important one.** The repo has three
ways of not building something — cannot test it (`ADR-001`), not valuable yet (the consideration
pile), someone else's job (`ADR-002`) — and none of them means *we do not want this to be possible*.
Account deletion is the canonical case: testable, present in the API, not post-1.0, and no agent
should ever reach it.

**Why it is a control and not a preference.** Toolsets, capabilities and allowlists are all
configuration, and configuration can be set wrong — `GOALS.md` failure mode #1 is a destructive call
nobody knew was destructive. An operation that was never generated cannot be enabled by a
misconfiguration, a bad default, or a later refactor that forgets a gate. It is the only layer of the
design that is not a runtime check, and it is therefore the only one that survives a gate failure.

A `Never` entry records **what** is refused, **why**, and **what would reopen it** — the same shape
`WAITING-FOR` uses, so a refusal is falsifiable rather than permanent by inertia.

### Inputs

1. **The API itself** — the OpenAPI specs and the `API-SURFACE.md` probes already in the repo.
2. **The official vendor MCP server**, where one exists. For Zendesk this is
   [issue #17](https://github.com/CloudSecurityAlliance/csa-zendesk/issues/17): a first-party server
   was announced at Relate 2026, `BUSINESS-CASE.md` makes a claim about its coverage, and
   `analysis/PRIOR-ART.md` contains zero mentions of it. **#17 is a dependency of this step**, not a
   loose end — what the vendor already covers well is evidence about what we need not curate.
3. **Third-party servers** — `analysis/PRIOR-ART.md` already surveys twelve, read from source.
4. **The official SDKs** — TODO **A1**, open: their union is 97 resources against our 125 families.
   The triage is where that reconciliation gets done rather than deferred again.

### 3b. The negative space — what the web interface can do and the API cannot

**The triage above partitions the API surface, and the API surface is not the capability surface.**
A capability reachable only through the vendor's web interface never appears in the operation
inventory at all, so the triage can neither admit nor refuse it. It is structurally invisible to a
pass that reads the API.

This is not hypothetical for the fleet. `ROSTER.md`'s North Star was moved from *API* coverage to
*capability* coverage precisely because of it: `csa-skilljar` promised the caller reaches "whichever
Skilljar API actually has the capability", which quietly assumes some API has it — and practice-exam
grading, reachable only through the web UI, falsifies that. **"100% API coverage" can be true while
the server is still incomplete.**

`analysis/UI-ACTION-MAP.md` already does the adjacent job — it enumerates the UI's vocabulary by
asking Zendesk's own `definitions.json` endpoints. That is the *positive* bridge: API shape to
human-recognisable action. It cannot find absences, because it asks the API what the API models.

**You cannot find an absence by reading the documentation of what exists.** Official documentation
describes the positive space; the gap is visible only from outside it.

#### Where the negative space is actually visible

| Source | What it reveals |
|---|---|
| **Complaints** — vendor forums, Stack Overflow, GitHub issues on client libraries, Reddit | "Is there an API for X?" answered "no, UI only" is the highest-signal artifact there is. People only ask after trying |
| **Third-party tool limitations** | Integrations that say "we cannot sync X because the API does not expose it" have already done this research |
| **The vendor's own known-limitations pages** | Sometimes documented, rarely near the API reference |
| **Evidence of an internal/private API** | The decisive case. If the web interface calls endpoints the public API does not publish, every capability behind them is unreachable by us |
| **Changelog and deprecations** | A capability recently removed from the API is a gap that used to not exist |

**The canonical example is Airtable**: an internal API powers the web interface and a separate
external API is what integrators get. **Comments exist only in the internal one** — so no amount of
reading the public API reference reveals that commenting is possible at all. A project that scoped
itself from the public reference would conclude Airtable has no comments.

#### What it produces, and what each entry forces

A list of **capabilities a human can perform in the web interface that no API operation reaches**.
Each entry is a decision, not a note:

- **Web automation, with drift detection.** `ROSTER.md` is explicit that an API route has a contract
  and a web route has none and breaks silently, so committing to web automation *without* a drift
  watcher is committing to silent breakage. `csa-skilljar`'s `scripts/check_upstream.py` is the
  pattern.
- **Out of scope, recorded.** The same shape as a `Never` entry — what is refused, why, and what
  would reopen it.
- **Wait**, with a `WAITING-FOR` trigger, where the vendor has signalled the gap will close.

#### Why this runs at the same time as everything else

Reviewing holistically is the point. A gap found after the tool surface is designed either gets
bolted on as an exception or gets silently dropped, and both outcomes are how an API-shaped tool
list ends up mistaken for a capability-complete one. The negative-space pass, the triage and the
classification are **one review, run together, before anything is generated.**

### Output

One table, per family, with a bucket and a recorded reason; plus the negative-space list from §3b. It feeds §4's classification: `Now` is
what gets classified on the four axes and generated; `Later` is classified but not generated, so
admitting it later is a build step rather than a redesign; `Never` and `Blocked` are neither.

### Granularity

**Per family, with per-operation exceptions.** 125 families is a tractable number of judgements; 822
is not. Most families are wholly in or wholly out. Where a family is mostly wanted but contains
something that is not — a destructive account-level operation inside an otherwise ordinary
administration family — the exception is recorded against the operation, and the family carries a
note saying it has one.

### This step generalises to the fleet

Every CSA MCP server wraps a vendor API larger than its useful surface, and every one of them has so
far decided scope implicitly. This triage — API plus prior art plus official server, bucketed into
now / later / never / blocked — belongs in the fleet's shared practice rather than in this repo
alone. Recorded here as the first instance; the fleet-level write-up is owed separately.

---

## 4. How the in-scope surface becomes tools

**Counts used in this spec:** 882 operations exist; **822 are reachable** after `ADR-001`'s exclusion
of the six untestable families. How many are *admitted* is §3's output and is expected to be smaller. `ADR-006` says "~700 reachable" and that figure predates the current
inventory — 822 is the number to use, and `scripts/check_counts.py`
([PR #16](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/16)) derives it rather than
trusting any of the three.

**The rule: a tool never spans two impact buckets.**

Every operation is classified on DEC-015's four axes:

| Axis | The question | Values |
|---|---|---|
| **Effect** | does state change? | read · write |
| **Reversibility** | can it be undone, and for how long? | reversible · reversible-for-a-period · irreversible |
| **Reach** | does the effect leave our boundary and touch a person? | internal · contacts-a-person |
| **Authority** | what must the caller hold? | the capability |

Tool boundaries are then drawn so that **every operation inside a tool shares all four values**.

> **Correction (2026-09-17, [ADR-016](../../../DECISIONS-ADR/ADR-016.md)).** This section originally
> continued: *"within a bucket, operations group by task and argument shape"* — assuming tools are
> built by **grouping** operations. A validation slice falsified that before any code was written.
> `PUT /tickets/{id}` is five impact levels in one operation, so one operation must back **several**
> tools, each narrower than it, and **the constraint on the request body is what makes a tool
> bucket-pure**. A tool is `(operation × constrained arguments)`. In the ticket-write path there are
> therefore *more* tools than operations, not fewer. Tools are atomic; sequencing lives in a
> workflow plugin.

Four consequences, and they are the reason to adopt it:

- **The MCP annotation becomes derivable rather than authored.** A tool spanning read and delete
  cannot honestly be annotated `readOnly`; forcing it produces exactly the `csa-skilljar` T16 failure
  that DEC-015 was written about.
- **The capability maps 1:1 to the tool**, so the gate sits *at* the boundary instead of inside it,
  and `GOALS.md`'s "the gate is proven by refusal" becomes one test per tool rather than a matrix.
- **`merge_tickets` sorts itself.** TODO **C6** records that merging closes the source ticket and
  needs `ticket.close`, not `ticket.write`. Under bucket purity it cannot sit in the write bucket;
  the classification places it rather than someone remembering to.
- **`ADR-008` already applies this principle** — the hatch is split into `zendesk_read` and
  `zendesk_request` precisely because one tool cannot span read and write. This generalises a rule the
  project already reached for.

### One artifact, two derived outputs

```
classification of all 822 operations
        ├──> capability set    (ADR-010: derived, not hand-listed)
        └──> tool list         (bucket purity + task grouping)
```

Both regenerate from the table. A hand-maintained tool list drifts from a hand-maintained gate table,
and the tests derived from each agree with it while both are wrong — `GOALS.md` failure mode #3.

**Expected size:** eight toolsets across four-to-six live buckets each puts the curated list around
**30–50 tools** — near the 51 of the widest server surveyed, well above the ~10 of the core loop.
Everything uncurated stays reachable through the hatch.

> **Correction (2026-09-18, Block 0c Task 6, [`analysis/SLICE-FINDINGS.md`](../../../analysis/SLICE-FINDINGS.md)).**
> This estimate assumed tools would be built by **grouping** operations — fewer tools than
> operations. ADR-016 replaced that assumption with **splitting**: a tool is `(operation ×
> constrained arguments)`, and one operation may back several tools. The validation slice tested
> this on `PUT /tickets/{id}` and got a **6:1 split** — one operation, six tools — driven entirely by
> that operation spanning five impact levels. `create_ticket`, `merge_tickets`, `get_ticket`,
> `search_tickets` and `update_trigger` stayed 1:1 in the same slice, so the true multiplier is not
> uniform; a full recount needs **B18**'s corrected per-operation classification before it can be
> exact. But the admitted "now" bucket (335 operations, 33 families,
> [`analysis/SCOPING-TRIAGE.md`](../../../analysis/SCOPING-TRIAGE.md)) includes several config-object
> families — triggers, automations, macros, views, ticket forms, workspaces — whose update operations
> have the same "one write endpoint, several impact levels" shape that produced the 6:1 split here,
> not one isolated case. If a meaningful fraction of them split the way the ticket write path did,
> the honest range is not "near 51" — it plausibly **exceeds** the widest server surveyed by a wide
> margin. This estimate should be treated as superseded pending a real recount, not as current
> guidance, and the design question it raises — a bucket-pure surface and a surface a model can
> usefully choose from may not be simultaneously achievable at Zendesk's actual granularity — is open
> and tracked as **B28**.

---

## 5. Build order and enable order are different things

**Only one of them is incremental.**

**Build: one pass, the whole in-scope surface.** Read, write, admin, everything §3 admitted. How the
admitted operations bucket into tools is a *global* design decision; design it against half the
surface and it gets redone. A good
basic design, decided once, then executed. Testing a half-built surface also does not surface what is
missing — the gaps only appear when the whole thing is there to exercise.

**Enable: earned, one rung at a time.** Verify read works, then write, then admin read, then admin
write. Steadily, not glacially.

The two tracks have opposite risk profiles — the build is broad with shallow risk per step, the
enablement is narrow with deep risk per step — which is why they take different cadences and
different gates.

### Build track, in dependency order

| | Work | Notes |
|---|---|---|
| **B0** | Foundations | [PR #12](https://github.com/CloudSecurityAlliance/csa-zendesk/pull/12) — green, `MERGEABLE`, `CLEAN`, 32 commits. `main` carries no code until it lands |
| **B0b** | OAuth public client, PKCE, one token file, `whoami` | **The critical path, with a hard deadline of 2027-04-30.** `ADR-015` removed the API token fallback and declined to stockpile tokens before the 2026-10-27 minting cutoff, so this is the only route to a live call. `ADR-009`; TODO **B11** owes the plan |
| **B0d** | **Tool-surface validation slice** — eleven tools over eight operations against `FakeBackend` | **Resequenced 2026-09-17 to run before B0b.** [ADR-016](../../../DECISIONS-ADR/ADR-016.md) was produced by starting this slice and finding that `PUT /tickets/{id}` is five impact levels in one operation. The slice needs no network, no credentials and no OAuth, and it tests the design B1–B5 derive from — so it is cheaper before 0b than after. Plan: `plans/2026-09-17-block-0c-tool-slice.md` |
| **B0c** | **Scoping triage and negative-space research** (§3, §3b) — every family bucketed now / later / never / blocked, plus the list of capabilities the web interface reaches and the API does not | One review, run together. Depends on [#17](https://github.com/CloudSecurityAlliance/csa-zendesk/issues/17) (probe the official server) and closes **A1**. Can run in parallel with B0b |
| **B1** | **Classification of the admitted surface** on the four axes | The keystone; everything below derives from it. Largely mechanical — the OpenAPI specs and `API-SURFACE.md` probes carry most of the input |
| **B2** | Generate the capability set **and** the tool list from B1 | `ADR-010` mandates the first; §3 adds the second |
| **B3** | Backend: the whole surface | From-scratch client per `ADR-002`. **Rate limiting lands here** — CINO [#53](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/issues/53) records this server as having none, and the quota is shared with CSA's running systems. **The drift watcher lands here too** (issue #17) |
| **B4** | Tool layer: ~30–50 curated tools plus the hatch | **Prompt-injection wrapping belongs here** (TODO **A4**) — ticket bodies are untrusted text and this is the primary risk |
| **B5** | Gate, three allowlists, refusal tests | One refusal test per tool, hand-written rather than derived from the gate table |

### Enablement track — earned, after the build

| Rung | What becomes possible | Granted |
|---|---|---|
| **E1** | Triage the live queue; propose everything, change nothing | read capabilities; `READ=*` |
| **E2** | Work tickets for real | + note, write; `WRITE=` test ids + self-created |
| **E3** | See the configuration | + admin read |
| **E4** | Improve the configuration — the second loop | + admin write; `ADMIN=` named objects |
| **E5** | Replies reach customers | + reach (`ticket.reply`) |
| **E6** | The agent runs with escalation | per `AUTONOMY-POLICY.md` |

A rung is earned by the previous one working as expected, not by elapsed time. Skills capture is not a
rung — it runs across all of them, and is the output that compounds.

---

## Decisions this creates

Three ADRs are owed. They are listed here so the changes are decisions rather than drift.

| | Decision | Relationship to existing ADRs |
|---|---|---|
| **ADR-011** | Allowlists select subject; the allowlist is a blast-radius control, not a security boundary | Adds a third axis to `ADR-006` |
| **ADR-012** | The hatch is the primary phase-one surface | Changes `ADR-008`'s posture; must reconcile with `GOALS.md` failure mode #2 |
| **ADR-013** | Tool boundaries never span an impact bucket | Likely an extension of `ADR-010`, sharing its derive-don't-hand-maintain argument |
| **ADR-014** | Scope is admitted per family, and some operations are refused outright | Formalises §3. Narrows `ADR-001` from "cannot test" to one of four reasons not to build |

## Open items this closes by construction

| | Was | Now |
|---|---|---|
| **B9** | `admin.write` granularity, deferred | `CSA_ZD_ALLOWLIST_ADMIN` — per-object, finer than any capability split |
| **C6** | `merge_tickets` mis-gated at `ticket.write` | Bucket purity places it at `ticket.close` |
| **B11** | Block 0b plan owed | Scheduled as B0b |

## Explicitly not in scope

Named so they are decisions rather than omissions.

- **The queryable mirror (B8)** stays post-1.0, and the instruction to check whether Customer360's
  existing Zendesk mirror can be queried before building a second one still stands.
- **Voice / Talk** stays post-1.0.
- **The six plan-gated families** stay behind `WAITING-FOR-001` on its existing triggers.
- **Parity with the official SDKs** remains a non-goal (`ADR-002`).

## How we would know this plan failed

1. **The classification table is hand-edited to make a tool work.** It is the single source both the
   gate and the tool list derive from; editing it to fix a symptom reintroduces failure mode #3 with
   extra steps.
2. **A rung is granted because the previous one took too long, rather than because it worked.** The
   ladder is the only thing standing between the config loop and an irreversible mistake at scale.
3. **Hatch usage does not fall as curated tools land.** Then curation is aimed at the wrong
   operations, and §2's defence against failure mode #2 has not held.
4. **The build is delivered in slices after all.** The bucketing gets re-carved, and the effort spent
   deciding it once is spent again.
5. **The negative-space list is empty.** For any vendor of this size it will not be, and an empty
   list means §3b was skipped rather than that the API is complete. "100% API coverage" is then
   true and the server is still incomplete — which is the exact failure `ROSTER.md`'s North Star
   was rewritten to prevent.
6. **Block 0b slips past 2027-04-30.** `ADR-015` deliberately removed the fallback, so there is no
   degraded mode to slip into — the project simply cannot reach Zendesk. That was the point of the
   decision, and it only works if the date is treated as real.
7. **The `Never` bucket is empty, or is quietly emptied later.** An empty refusal list means the
   triage did not happen — a vendor API this size always contains something an agent should not be
   able to reach. Moving an entry out of `Never` is a decision with a named reason, not a
   convenience during implementation.
