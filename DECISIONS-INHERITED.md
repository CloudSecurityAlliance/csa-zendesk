# Fleet decisions, and what this project did with them

CINO-Platform-Engineering's `DECISIONS.md` holds decisions that apply across CSA software. This file
records which of them this project has **considered**, and what happened. See
[DECISION-LOGGING.md](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/DECISION-LOGGING.md)
for the convention.

**Silence means not yet considered** — a decision absent from this file has not been assessed here,
which is information rather than an absence. A deviation names *why* and *what would bring us back*.

| Decision | State | Here |
|---|---|---|
| **DEC-012** uv as the toolchain | **adopted** | `uv` throughout; CI, the pre-commit hook and every documented command use it. |
| **DEC-014** HTML→Markdown at the model boundary | **adopted** | Superseded in practice by DEC-018, which widened it — see below. |
| **DEC-015** four axes, reach is one | **adopted** | `analysis/operation-classification.csv` classifies on them, and `reach` is a first-class flag on `ToolSpec` rather than a comment. |
| **DEC-016** red absent, orange needs a second grant | **adopted** | `reply_publicly` is the worked example: capability **and** `CSA_ZD_ALLOW_REACH` **and** the write allowlist, in three places, none sufficient alone. |
| **DEC-017** within green, get work done by default | **adopted** | Allowlists are fit-and-blast-radius controls, not security boundaries; `CSA_ZD_ALLOWLIST_READ=*` is the normal triage posture. |
| **DEC-018** convert HTML on ingest | **adopted** | `_markdown.to_markdown` at the `Backend` seam, on all seven envelope-returning methods. First implementation in the fleet. |
| **DEC-020** a refusal is a negotiation | **partly adopted** | The *shape* is in use — `solve_ticket`'s refusal names the argument that would proceed and carries the field ids. The `accept_risks` contract is **not built**; no content class is refused-with-override yet. |
| **DEC-021** say what you changed | **adopted** | A `transformations` array names field, action and rule, absent when nothing happened. |
| **DEC-022** publish to PyPI | **adopted** | `csa-zendesk` on PyPI via Trusted Publishing with PEP 740 attestations. |
| **DEC-023** `0.X.Y` until we mean 1.0.0 | **adopted** | At 0.2.0. Movement 7 (audit → 1.0.0) not reached. |
| **DEC-019** portable credential format | **not adopted yet** | The on-disk store predates it and carries no `instance` field, which is [#63](https://github.com/CloudSecurityAlliance/csa-zendesk/issues/63). Adopting the format would fix that as a side effect. |
| **DEC-013** platform tiers | **not assessed** | No Windows run has happened here. Recorded so the gap is visible rather than assumed. |

## Deviations

### `reply_publicly` takes a flat shape, not the `comment` dict the tool table predicted

**What the norm was.** `tools.py` declared `reply_publicly` with `check=_force_public(True)`, a
constraint that takes a `comment` dict and overwrites its `public` key. The note beside it said the
future Backend method was "expected to take the `comment` shape this helper was written for."

**What this project does.** `reply_publicly(ticket_id, body, uploads)` — flat, with `public: True`
hardcoded in the backend and no `public` parameter anywhere.

**Why.** The same file already explains why `add_internal_note` moved off that helper: the safety
property is *structural, not checked* — there is no `public` argument for a caller, **or for an
instruction injected from ticket content the model is reading**, to set. That argument is stronger
for the tool that can actually reach a customer, so taking the weaker guarantee there would have
been backwards. Overwriting a key is weaker than the key not existing.

**What would bring us back.** Nothing foreseeable — this is strictly stronger. If a future tool
genuinely needs caller-supplied comment structure, it should carry its own argument rather than
reviving a shared dict.

### The solve refusal reads ticket-field configuration, which is arguably rung E3

**The norm.** The enablement ladder puts "see the configuration" at E3; this server is registered at
E2/E5 for ticket work.

**What this project does.** On a solve refused for missing required fields, `solve_ticket` reads
`/api/v2/ticket_fields` to turn the labels Zendesk names into the ids its API takes.

**Why.** Without it the refusal names fields by label while the API needs ids, so a caller who is
already stuck has to go to another system. **Every failure of the lookup is swallowed** — it is a
courtesy, never a dependency, and a credential that cannot read field configuration still gets the
actionable refusal.

**What would bring us back.** E3 landing. At that point the lookup should be gated on the admin-read
capability and degrade to today's behaviour when it is not granted — tracked as F11's successor in
`TODO.md`.
