# Client-side control battery — 2026-09-24

## The method, and why the account makes it clean

An earlier note framed testing on an unrestricted account as a *limitation*. For server-side
authorization it is. **For the client-side controls it is the opposite: it is what makes the result
unambiguous.**

If the account could not perform the operation anyway, a refusal proves nothing — Zendesk might have
refused it regardless, and the two are indistinguishable from outside. When the account **can** do
the thing and it is blocked anyway, the block is provably ours.

So the protocol is: *attempt operations the account is fully entitled to perform, and see whether
our own layer stops them.*

This also corrects a flaw in an earlier probe. The first allowlist check used a **non-existent**
ticket id, which was a bad test: a refusal could have been our allowlist or an impending `404`, and
nothing distinguished them. Every subject below is a **real ticket this account can write to.**

## The battery

Two tickets. **A** is in `CSA_ZD_ALLOWLIST_WRITE`; **B** is not. Both exist, both are writable by
this account, both are ordinary tickets to Zendesk.

| # | call | subject | fields | expect | result |
|---|---|---|---|---|---|
| 1 | `update_ticket` | **B** (not allowlisted) | legal | refuse | **refused** — named the subject and the variable to change |
| 2 | `add_internal_note` | **B** (not allowlisted) | n/a | refuse | **refused** — same control, different tool, same message shape |
| 3 | `update_ticket` | **A** (allowlisted) | `status` + `priority` | refuse | **refused** — named the offending field and listed the nine permitted |
| 4 | `update_ticket` | **A** (allowlisted) | `priority` + `tags` | allow | **applied**, both changes on the audit |
| 5 | `list_comments` | **B** (not allowlisted) | n/a | allow | **succeeded** — `READ=*`, so the write allowlist does not bleak into reads |

Every refusal happened **before any API call**. Zendesk would have accepted all five.

## What the battery establishes

**Two controls exist and they are distinct.** Tests 1–2 refuse on the *subject*; test 3 refuses on
the *field*, on a subject that is permitted. A single conflated check could not produce both
behaviours, and either test alone would not have shown the difference.

**Neither control is a blanket refusal.** Tests 4 and 5 are the negative controls, one per axis. A
gate that refuses everything looks identical to a correct gate until you show it permitting
something, and this is the part that is easy to leave out. Test 3 and test 4 differ only in **which
fields** were sent — same tool, same ticket, same credential, one second apart.

**The field check is atomic.** Test 3 sent one illegal field (`status`) alongside one legal one
(`priority`). The call was refused **in whole** — `priority` was not quietly applied on the way
past. A partial write would have been a real defect: it would mean a caller could smuggle a legal
change through by attaching it to an illegal one, and the audit would show a change nobody
authorised.

**The refusals name the remedy.** Each message says what was refused, why, and what to change —
`CSA_ZD_ALLOWLIST_WRITE` by name in 1–2, the nine permitted fields enumerated in 3. A refusal a
caller cannot act on is a dead end, and these are not.

## What it does not establish

Nothing about Zendesk's own enforcement — by construction, since the whole method depends on the
account being entitled to everything attempted. The server-side half still needs a restricted
account (**F9**).

It also says nothing about controls with no live coverage. **C8** is the sharp one: the subject
check reads a single `ticket_id`, so `merge_tickets(ticket_id=<allowed>, ids=[<not allowed>])`
passes scope and closes a ticket outside the allowlist. That is a client-side fail-open — exactly
the class this battery *can* catch — and it is untested here only because `merge_tickets` has no
`Backend` method yet. **It should be the first addition to this battery when it does.**
