# Security

## Reporting a vulnerability

Email **security@cloudsecurityalliance.org**, or open a private security advisory through
GitHub's *Security → Report a vulnerability* on this repository. Please do not open a public
issue for a suspected vulnerability.

## Status

**Nothing is implemented yet.** This document states the threat model the design must answer,
because writing it after the design would mean discovering the constraints too late. Sections
describing code describe *intended* behaviour and say so.

## The primary risk: prompt injection through ticket content

A Zendesk ticket body is **text written by a stranger who has an interest in the outcome**, and
reading it is the entire purpose of this software. That makes it the sharpest instance of the
untrusted-content problem, worse than the sibling projects face with document comments or course
material:

- Anyone who can email a support address can put text in front of the model.
- The requester frequently *wants* something from the organisation — a refund, an escalation, a
  password reset — so there is a motive, not merely an opportunity.
- Free-text custom fields, subjects, attachment filenames, satisfaction-rating comments and
  side-conversation bodies are all injection surfaces, not just the main comment thread.
- Macros are pre-written text that a model may be asked to apply; the reference tenant has
  hundreds, written over years by many people.

**Intended controls.** Zendesk-origin text is wrapped in generated delimiters before it reaches
the model, screened for suspicious content, and never treated as instruction. A candidate
library for this is under evaluation (`TODO.md` A4). The server's instructions will state
plainly that ticket content is data to report on, never a command to act on, and that mutating
actions happen only on the user's explicit instruction.

**What we will not rely on.** Asking the model nicely. The wrapping has to be structural, on by
default, and applied at the boundary rather than per-tool.

## Credential exposure

**Today's development credential is the worst case**: a Zendesk **API token** that is
unscoped, carries full administrator rights across every endpoint, and **bypasses the
two-factor authentication enabled on the account**. It lives in a gitignored `./.env`.

This is a deliberate, temporary trade for exploration speed, and it is why every probing script
in `scripts/` issues **GET requests only**.

**The shipped design authenticates with OAuth**, for reasons beyond the deprecation:

- Zendesk offers **54 granular scopes**, so a deployment can be narrowed to what it needs.
- Per-operator `authorization_code` means writes carry the operator's identity — their name on a
  public reply, their entry in the audit log — which `client_credentials` cannot do, because
  those tokens are attributed to whoever created the client.
- API tokens are being retired: **2026-10-27** no account can create one, **2027-04-30** all
  existing tokens stop working.

**Never**: interpolate a credential into an error message, a log line, or a `__repr__`; commit
`.env`; or hardcode a tenant subdomain (it is both a leak and a footgun — `scripts/zd.py`
refuses to run without `ZENDESK_SUBDOMAIN`).

## Authority: scopes are not a policy

OAuth scopes are a **ceiling on a token**, fixed when the client is registered. They cannot
express "may read tickets, may write Help Center articles, may never touch users" for a
particular deployment, and changing them means re-registering.

No Zendesk MCP server surveyed has anything more than scopes. The intended control is a
**fail-closed capability policy** wrapping the backend seam: a method with no declared
capability is refused rather than delegated, so a newly added capability arrives *off*. Writes
are off by default.

**Zendesk's own validator is a schema check, not a supervisor.** It reliably refuses malformed
records — it enforces required-fields-on-solve server-side and names each violation. It has no
opinion on whether an action is *wise*: it will accept a public reply that should have been an
internal note, a valid-but-wrong field value that lands in audit reporting, or four hundred
solves in a loop. Those are the failures the policy layer exists for.

## Irreversible actions

Ranked by how bad a mistake is, because the design should gate them differently:

| Action | Reversible? |
|---|---|
| Public comment on a ticket | **No** — it emails the requester on send |
| Anonymising a user | **No** |
| Deleting an organization or ticket | Effectively no |
| Merging tickets | Painful |
| Solving a ticket | Yes — reopen |
| Field edits | Yes — audit trail records the change |

`comment.public` is one boolean away from `comment.public: false`, and the two differ by whether
a stranger receives an email. Its default when omitted is **not yet verified** (`TODO.md` C4)
and no code should assume one.

## Data handled

- **Ticket content** — customer correspondence: names, addresses, and whatever a requester
  pasted in. The reference tenant also runs quality-management and incident-response workflows
  on Zendesk, so ticket content can constitute audit evidence.
- **Tenant configuration** — group structure, business rules, support addresses, installed apps.
- **Nothing is persisted by design.** No caching, no token file for `client_credentials`, no
  local attachment cache. Probe artifacts record **counts and shapes, never rows**.

A separate, private backup of the reference tenant's data and configuration already exists and
is not this project's concern.

## Publication controls

This repository is public, and two rules are enforced mechanically rather than remembered:

1. **Facts about Zendesk are public; facts about a tenant are not.**
2. **A survey of an ecosystem names no individuals.**

`scripts/check_public_safe.py` enforces both over every tracked file, in two tiers — structural
patterns in the script, literal terms in a gitignored file — because an earlier version
hardcoded the literals and became a searchable index of what it existed to hide. It reports
reduced coverage rather than a false pass when the private list is absent, and
`.githooks/pre-commit` runs it. It is mutation-tested in both directions.

## Known gaps

| Gap | Status |
|---|---|
| Development credential is unscoped admin and bypasses 2FA | Accepted for exploration; GET-only discipline compensates. OAuth is the shipped path. |
| `comment.public` default unverified | `TODO.md` C4 — the highest-value unknown, and cheap to resolve |
| Prompt-injection controls not built | `TODO.md` A4; the design must not proceed far without settling it |
| No CI gates yet | The repository is public before it meets the CSA public-repo standard — lint, type-check, test matrix, coverage floor and security scan are owed |
| `agent_conditions` structure is undocumented | Reverse-engineered and validated against the enforcer; kept as a conformance check so upstream changes fail loudly |

## Review

Last reviewed **2026-08-31**. Next review when implementation begins, or when the credential
model changes — whichever is first.
