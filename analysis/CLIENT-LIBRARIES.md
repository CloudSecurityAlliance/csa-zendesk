# Existing Zendesk client libraries — build or adopt?

Assessed 2026-08-31. Clones and wheels in `other-zendesk-libraries/`, gitignored.

## 1. There is no official Python SDK

Zendesk maintains client libraries for **PHP** and **Ruby** only. Its own Python page points at
two community libraries rather than shipping one.

| | Stars | Last push | State |
|---|---:|---|---|
| `zendesk_api_client_php` (official) | 343 | 2026-08-13 | maintained |
| `zendesk_api_client_rb` (official) | 409 | 2026-07-23 | maintained |
| `zenpy` (community, Python) | 370 | 2026-08-05 | active, 50 open issues |
| `zdesk` (community, Python) | 98 | 2026-07-06 | **archived** |
| `python-zendesk` (generated, Python) | — | PyPI 2026-05-13 | v0.3.1, third-party generated |

So for Python the field is **one active hand-written library and one generated one.**

## 2. `zenpy` — mature, and the wrong shape for this project

279 endpoints, `python_requires>=3.9`, an ORM-style object mapper over the API.

**What it does well, and what it independently confirms.** Its exception hierarchy is evidence
from another party that our findings are real:

- `SearchResponseLimitExceeded` — it hit the 1000-result search ceiling and named an exception
  after it, citing Zendesk's own breaking-change notice.
- `RateLimitError` and `RatelimitBudgetExceeded` — proper `429` handling with `Retry-After`
  parsing, plus a budget concept for staying under the account limit.
- Cursor pagination is supported, opt-in per call (`cursor_pagination=` → `page[size]`).

**Why it is nonetheless a poor foundation here:**

- **Effectively untyped.** Three type annotations in the whole of `api.py`, and no `py.typed`
  marker. Our MCP layer needs `TypedDict`s for structured output, so every response would be
  re-typed by hand on top of an untyped mapper — worse than typing raw JSON once.
- **It caches by default** (`cachetools`). Both sibling projects deliberately do not cache, for
  the same reason: a self-invalidated cache goes stale in exactly the multi-actor, live sessions
  these tools are used in.
- **The object mapper is the thing we do not want.** Our `Backend` returns **raw upstream
  envelopes**, with shaping deferred to the delivery layer. That is the seam the whole policy
  design hangs off. An ORM sits precisely where we have deliberately put nothing.
- **Partial Help Center coverage** — 7 of the ~30 documented families (articles, categories,
  sections, translations, user segments, users, incremental). Half of our 1.0 scope is Help
  Center.

## 3. `python-zendesk` — generated, and inherits the spec's defects

Speakeasy-generated from the Zendesk Support OpenAPI spec — the same document in `specs/`.
**93 modules, 1173 methods, 839 pydantic models**, on `httpx` + `pydantic`. Modern stack, and a
great deal of surface for free.

Its own description says it "slightly modified [the spec] to comply with the OpenAPI spec … as
well as adding **partial** pagination support", which is a third party independently reporting
the same defect we documented: the spec does not describe Zendesk's pagination.

That is the core problem with adopting it, and it generalises to any generated client:

- **Support API only.** No Help Center, no Voice. Help Center is half of 1.0.
- **Pagination is right exactly where the spec is right** — 48 of 333 Support GETs declare it.
  Elsewhere the generated methods have no cursor parameters, because there was nothing to
  generate them from.
- **The 422 diagnosis is not modelled.** Eleven error classes, and nothing references
  `details.base[]` — the array carrying the field id and type per violation, which our
  experiment established is the entire actionable content of a validation refusal.
- **v0.3.1, no repository URL on PyPI, no stated maintenance commitment.** A regeneratable
  artifact rather than a supported library.

**A generated SDK inherits every defect of the document it was generated from**, and we spent
two days establishing that this particular document is wrong about pagination, wrong about
error shapes, and missing about a dozen Help Center families.

## 4. The architectural point

The value a client library sells is **object mapping and typed models**. That is the layer this
project deliberately does not have at the seam:

```
Backend (Protocol)   returns RAW upstream envelopes
    ^ wrapped by
PolicyBackend        gates by capability, fails closed
    ^ consumed by
Client               thin typed surface
    ^ consumed by
mcp/_tools/*.py      shapes envelopes into TypedDicts
```

Adopting a mapper would mean unwrapping its objects back into envelopes for the policy seam, or
moving the seam above it and losing the uniform wrapper that makes fail-closed gating possible.

What we would actually use from a library — URL construction, `429`/`Retry-After` handling, and
the per-endpoint rate buckets — is the cheap part. `scripts/zd.py` is already 40 lines and does
requests, auth, and error-body preservation.

## 5. Recommendation

**Build the HTTP layer; mine the libraries for solved problems rather than depending on them.**

Specifically worth taking:

- zenpy's rate-limit budget concept, and its `Retry-After` parsing.
- Its `SearchResponseLimitExceeded` as precedent for naming a typed error after a documented
  product limit rather than surfacing a bare 422.
- `python-zendesk` as a **cross-check on our generator**: where our `Backend` generation and its
  1173 generated methods disagree about a signature, one of us has misread the spec. That is a
  free conformance signal and costs nothing to consult.

Worth reconsidering if: Zendesk ships an official Python SDK; `python-zendesk` grows Help Center
coverage and a maintenance commitment; or our own HTTP layer exceeds roughly 400 lines, at which
point we are writing a client library by accident and should say so.

**Not decided here.** This is analysis; the build-or-adopt call is ADR material and has not been
made.
