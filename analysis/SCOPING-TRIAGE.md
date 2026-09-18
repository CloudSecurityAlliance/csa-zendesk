# Scoping triage — what we build, what we defer, what we refuse

**Date:** 2026-09-17
**Status:** First pass, for review. Family-level with per-operation exceptions.
**Specified by:** [`docs/superpowers/specs/2026-09-17-csa-zendesk-whole-project-design.md`](../docs/superpowers/specs/2026-09-17-csa-zendesk-whole-project-design.md) §3 (B0c)

Every count below is produced by `scripts/triage.py` from `scope-triage.csv` and
`scope-triage-exceptions.csv`. None is typed. A family with no assignment is an **error**, not a
default — silently defaulting is how an operation gets built with nobody having decided it should be.

```
bucket     families  operations   share
now              33         335    40.8%
later            56         387    47.1%
never             9          48     5.8%
blocked          10          52     6.3%
total           108         822
```

**Two fifths of the reachable surface is admitted.** The other three fifths is deferred, refused or
plan-gated — and before this pass, all of it was implicitly in scope.

---

## Never — 9 families and 8 individual operations

The bucket that did not exist before. These are not untestable, not post-1.0, and not someone else's
job. They are operations **this tool should not make possible**, and because they are never
generated, no capability misconfiguration, bad default, or future refactor that forgets a gate can
reach them. It is the only layer of the design that is not a runtime check.

### Whole families

| Family | Ops | Why refused |
|---|---:|---|
| **API Tokens** | 4 | Minting or revoking API credentials. An agent that can mint a credential can escape its own gate — this is the clearest case in the list |
| **OAuth Clients** | 7 | Credential infrastructure, same reason |
| **OAuth Tokens** | 5 | Same |
| **Grant Type Tokens** | 1 | Same |
| **User Passwords** | 3 | Setting or changing a person's password is credential manipulation, not support |
| **Custom Roles** | 5 | Changes who may do what in Zendesk. Privilege escalation by definition, and a human decision |
| **Sessions** | 8 | Deleting sessions force-logs-out real people; reading them is surveillance of staff. Neither is support |
| **Deletion Schedules** | 5 | Configures automatic permanent deletion of customer data. Irreversible by design and on a timer |
| **Reseller** | 2 | Account provisioning and billing. Nothing in the support loop needs it and the blast radius is the account |

Four of the nine are credential infrastructure. That is not a coincidence: the ability to mint a
credential is the ability to become a different principal, which defeats every other control in the
design at once.

### Individual operations inside admitted families

| Operation | Why |
|---|---|
| `DELETE /deleted_tickets/{ticket_id}` | Permanently purges an already-deleted ticket — the one deletion with no undo |
| `DELETE /deleted_tickets/destroy_many` | The same with a multiplier |
| `DELETE /deleted_users/{deleted_user_id}` | Permanently purges a person's record; a GDPR-shaped act that belongs to a human |
| `PUT /users/{user_id}/merge` | Irreversibly merges two people. Wrong on a real customer, no undo |
| `POST /organizations/{id}/merge` | Irreversibly merges two organizations |
| `PUT /tickets/{ticket_id}/mark_as_spam` | Suspends the requester. Punitive, and it reaches a real person |
| `DELETE /suspended_tickets/{id}` | Destroys a suspended ticket before anyone has read it |
| `DELETE /suspended_tickets/destroy_many` | The bulk form |

`Tickets`, `Users`, `Organizations` and `Suspended Tickets` are all admitted families. Eight
operations inside them are not, which is why the triage needs per-operation exceptions rather than
family-level assignment alone.

**Each of these is reopenable**, on the same terms `WAITING-FOR` uses: a stated need, a named person
accountable for the call, and a test that exercises it somewhere that is not production. A refusal
that cannot be revisited is a decision nobody made on purpose.

---

## Now — 33 families, 335 operations

Admitted because they serve one of the two loops the server exists for.

**Working the queue (22 families).** `Tickets`, `Ticket Comments`, `Ticket Audits`, `Ticket Metrics`,
`Ticket Metric Events`, `Users`, `Organizations`, `Groups`, `Group Memberships`,
`Organization Memberships`, `Requests`, `Search`, `Saved Searches`, `Views`, `Tags`, `Ticket Fields`,
`Ticket Forms`, `Custom Ticket Statuses`, `Attachments`, `Locales`, `Suspended Tickets`,
`Satisfaction Ratings` + `Satisfaction Reasons`.

**Improving the configuration (9 families).** `Macros`, `Triggers`, `Trigger Categories`,
`Object Triggers`, `Automations`, `SLA Policies`, `Dynamic Content`, `Dynamic Content Item Variants`
— plus `Views`, `Tags`, `Ticket Fields` and `Ticket Forms`, which serve both loops.

**Machinery (2).** `Job Statuses` (ADR-007 surfaces Zendesk's own async jobs) and `Basics` (`whoami`,
the always-on `context` toolset).

**Cross-check against prior art.** The twelve surveyed servers converge on `get_ticket` (11 of 12),
`create_ticket` (8), `update_ticket` (8), `get_organization` (6), `get_user` (6), `list_macros` (6),
`list_views` (6), `search_users` (5). **Every family behind those tools is in `now`.** No surveyed
server implements anything from a `never` family. The field agrees with the admission and is silent
on the refusals — which is the shape to expect, since nobody builds what nobody asks for.

---

## Later — 56 families, 387 operations

Classified, not generated. Admitting one later is a build step, not a redesign.

The three large clusters:

| Cluster | Families | Ops | Why deferred |
|---|---:|---:|---|
| **Help Center** | 18 | 182 | `GOALS.md` has it medium-term. The first loop is tickets and configuration. `Content Subscriptions` alone is 26 operations and `Votes` 19, for community features with no stated need. TODO **A6** — whether anyone localises — is still unanswered, which defers `Translations` (17) on its own terms |
| **Custom objects** | 6 | ~53 | No custom objects are defined at CSA today. The whole cluster defers as a unit |
| **Routing and channels** | 8 | ~45 | `Skill Based Routing` (18+3) is not configured; `X Channel`, `Channel Framework`, `Conversation Log` and `Omnichannel Routing Queues` are channels we do not operate |

Two deferrals worth naming individually because they look like `now` and are not:

- **`Incremental Export`** (7) — the natural tool for corpus analysis, and genuinely wanted. Deferred
  because it is the operation most likely to pull the project toward TODO **B8** (the queryable
  mirror), which has its own unresolved question: whether Customer360's existing Zendesk mirror can
  be queried instead of building a second one.
- **`Account Settings`** (4) and **`Support Addresses`** (6) — configuration-loop-adjacent, but they
  change account-wide behaviour rather than ticket handling. High consequence, low frequency: the
  wrong shape for the first turn of a loop whose whole point is iterating quickly.

---

## Blocked — 10 families, 52 operations

`ADR-001`'s exclusion, unchanged, on `WAITING-FOR-001`'s existing triggers. The five ITAM families
(23 operations) plus `Group SLA Policies`, `Workspaces`, `Ticket Form Statuses`, `Audit Logs` and
`Service Catalog Items`.

`WAITING-FOR-001` already names `Audit Logs` as the likeliest to reopen — *"who changed what in
Zendesk, and when" is a question a security organisation eventually asks* — and three operations is a
small thing to want. Nothing here changes that.

---

## What this pass found in the existing classification

`operation-classification.csv` needs a correction pass before B1 derives anything from it. Three
problems, all the same shape: **a value assigned per family has leaked onto operations it cannot
describe.**

1. **15 of the 17 `outward_facing=yes` rows are reads.** A `GET` contacts nobody. The axis is being
   used to mean *"belongs to the end-user-facing surface"* — `Requests`, `Email Notifications` —
   rather than DEC-015's Reach, *"does the effect leave our boundary and touch a person"*.

2. **The actual reach operation is not in the table at all.** There is no
   `POST /api/v2/tickets/{id}/comments`: in Zendesk you add a public reply by `PUT`ting the ticket
   with a comment object. So the single operation this project's entire design is built around —
   *"a public reply cannot be unsent"* — is **a parameter on ticket update**, and cannot be
   classified at operation granularity.

   DEC-015 anticipated exactly this: *"Reach may be a property of the call rather than the tool…
   only the call that requests contact is refused."* And `ADR-003` already resolves it at the tool
   layer by making public replies their own tool rather than a flag. What is missing is that the
   classification table has no way to say so, so the gate cannot be derived from it for this case.

3. **Four reads are flagged irreversible** — two `suspended_tickets` GETs and two `deleted_users`
   GETs. Family-level bleed again.

None of this blocks the triage, because the triage asks *should this exist* and these axes answer
*what does it cost*. It does block B1, which derives both the capability set and the tool list from
this table. **Fix the table before generating anything from it.**

---

## Reproducing and changing this

```
python3 scripts/triage.py
```

Edit `scope-triage.csv` to move a family, `scope-triage-exceptions.csv` to carve out an operation.
The script fails if a family is unassigned, if a bucket name is unknown, or if an exception matches
no operation — so the three ways this drifts silently are all errors instead.
