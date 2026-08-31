# Prior art: the Zendesk MCP server ecosystem

Twelve public Zendesk MCP servers, surveyed 2026-08-30/31 and read from **source** rather than
from READMEs, which drift.

**Servers are referred to as A–L, not by name.** This is a survey of an ecosystem to work out
what is unsolved, not a review of anyone's project. Most of these are individual side projects
built in the open and given away; several solve problems nobody else has touched, and this
document exists because reading them was useful. Ranking them by name in a corporate repository
would be both unkind and beside the point. The alias map is kept privately.

Clones are gitignored — other people's code under their own licences. Regenerate the data with
`scripts/survey_tools.py` → `analysis/prior-art-tools.json`.

---

## 1. Shape of the field

GitHub carries **62** repositories matching Zendesk + MCP. These twelve were selected for
reach, architectural variety, and relevance.

| | Tools | Language | Cursor paging | Tool annotations | Naming |
|---|---:|---|---|---|---|
| A | 51 | TS | yes | yes | bare |
| B | 48 | JS | no | no | bare |
| C | 33 | Py | yes | no | bare |
| D | 29 | Py | no | yes | prefixed |
| E | 27 | Py | no | no | bare |
| F | 26 | Py | no | yes | prefixed |
| G | 19 | TS | yes | yes | prefixed |
| H | 9 | Py | no | yes | bare |
| I | 8 | TS | no | no | prefixed |
| J | 7 | Py | no | yes | bare |
| K | 4 | Go | no | no | bare |
| L | 2 | TS | no | no | bare |

**Reach and coverage are inversely correlated.** The most widely adopted server in the set — by
an order of magnitude in stars and forks — ships seven tools. The widest surface has almost none.
Adoption here tracks packaging, timing and discoverability rather than capability, which is worth
knowing before treating popularity as a signal about design.

## 2. There is no parity target

Normalising names across the field (`zendesk_get_ticket` ≡ `get_ticket` ≡ `Zendesk:get_ticket`):

- **155 distinct tools** across twelve servers
- **110 of them — 71% — exist in exactly one server**
- Only **six** appear in three or more:

| Servers | Tool |
|---:|---|
| 11 / 12 | `get_ticket` |
| 8 | `update_ticket` |
| 8 | `create_ticket` |
| 6 | `get_user` |
| 6 | `get_organization` |
| 6 | `list_views`, `list_macros` |

A long tail at 3–5 covers `search_users`, `list_tickets`, `search_tickets`, `get_view`,
`list_groups`, `search`, `get_ticket_comments`, `get_view_tickets`, `list_organizations`,
`get_article`, `list_articles`, `list_triggers`, `list_automations`, `add_private_note`,
`download_attachment`, `get_linked_incidents`.

**Conclusion for naming.** "Adopt the names people already know" resolves to about six, and they
are the obvious ones. Use `get_ticket` / `create_ticket` / `update_ticket` / `get_user` /
`get_organization` / `get_ticket_comments` in exactly those spellings and anyone arriving from
another server keeps their muscle memory. Beyond that there is nothing to be compatible *with*,
so the rest of the surface is a design question rather than a compatibility one.

**Bare `verb_noun`, not a `zendesk_` prefix.** Prefixed appears in 4 of 12 and in none of the
three widest surfaces; bare matches the most widely adopted server, the widest one, and both CSA
sibling libraries. The MCP client already namespaces by server, so a prefix buys nothing.

## 3. Where the 155 tools go

| Domain | Distinct tools | In 3+ servers |
|---|---:|---:|
| help center | 29 | 2 |
| ticket read | 20 | 6 |
| automation rules (triggers / automations / SLA / webhooks) | 12 | 3 |
| ticket write | 11 | 2 |
| attachments | 11 | 1 |
| users | 11 | 2 |
| views / queues | 10 | 3 |
| macros | 8 | 1 |
| time & reporting | 8 | **0** |
| orgs | 7 | 2 |
| groups | 7 | 1 |
| comments / notes | 5 | 1 |
| search | 2 | 1 |
| auth / meta | 2 | 0 |

**Time and reporting has eight distinct tools and zero consensus.** Every server that reaches for
reporting invents its own vocabulary. That is the clearest available signal that the analytical
use case is unserved rather than solved differently — and it is the use case we have.

## 4. What is genuinely unaddressed

Verified against the parsed surfaces rather than inferred from counts:

- **Incremental export.** No server exposes it. It is the only mechanism for bulk retrieval that
  is not subject to the search ceiling, and it carries its own tight rate bucket.
- **Job statuses.** No server exposes them, so no server can safely offer a bulk write — the
  result of one is a job id, not an outcome.
- **Side conversations.** No server exposes them.
- **Capability policy.** No server has one. OAuth scopes, fixed at client registration, are the
  only control anywhere in the field. Nothing lets an operator say "may read tickets, may write
  Help Center articles, may never touch users" — and the API offers 54 granular scopes to bind
  such a thing to.

Two things were on this list in the first pass and should not have been — see §7.

## 5. Patterns worth adopting

Described without attribution; each is one server's idea and each is good.

**Store the response, query it out of band.** Read tools write the raw response to a file and
return a path plus a summary; a separate tool runs `jq` against that file, backed by a library of
named queries per response type. A large result set therefore never has to transit the model's
context in order to be analysed. This is the most direct answer anyone in the field has to "the
interesting questions span thousands of tickets", and it sidesteps the search result ceiling
entirely, because paging goes to disk rather than to context.

**Treat prompt injection as a dependency, not a disclaimer.** One server wraps every piece of
Zendesk-origin text in generated delimiters before it reaches the model, screens it for
suspicious content, and ships this **on by default** with a per-record allowlist to opt out.
Nobody else in the field does anything at all here. Zendesk is the worst possible place to skip
it: a ticket body is text written by a stranger with an interest in the outcome, and reading it
is the entire point of the product.

**Drive authorization through tools rather than a CLI.** Begin- and complete-authorization tools
let the model walk the user through OAuth consent in the conversation, rather than telling them
to go and run something in a terminal. `csa-google-workspace` reached the same conclusion
independently with its `authenticate` tool.

**Rotate refresh tokens carefully.** Atomic writes under a lock file, and retry only on an
explicit invalid-token response — so a permission error stays visible as a permission error
instead of looking like auth flakiness.

**Prefer per-operator `authorization_code` for anything that writes.** Tokens minted by
`client_credentials` are attributed to whoever created the OAuth client, so every operator's
public replies and audit entries would name one administrator. Where a human's name goes on the
output, per-operator identity is the whole point.

**A Help-Center-only server is a coherent product.** One server in the set does nothing else. Our
Help Center surface should stand on its own behind its own capability gate, not be an appendix to
tickets.

## 6. Patterns to note and not copy

**A generic request passthrough.** One server exposes a single tool taking a method and path,
which makes the whole API reachable without hundreds of tools. It also discards structured
output, tool annotations and any prospect of capability gating, and hands a model an
arbitrary-request primitive pointed at whatever the credential can reach. If we offer one it must
be gated to explicit method/path patterns, and it cannot be the primary surface.

**A nine-tool attachment subsystem.** Store, download-and-extract, read, search, delete-cached.
Real work on a real problem, since attachments are often where the answer is. But nine tools of
surface for it, and a local cache of customer attachments is a data-retention decision rather
than a convenience.

## 7. Correction to the first pass

Two claims made after the first survey were **wrong**, and both failed the same way — asserting
absence on the basis of a survey that had only counted tools rather than read them:

| Claimed | Actually |
|---|---|
| nobody exposes ticket metrics | two servers do |
| nobody exposes satisfaction ratings | one server does |

The three in §4 were re-verified against parsed surfaces and hold. The lesson is narrow and
worth keeping: *a count establishes how many, never which* — and "nobody does X" is a claim about
which.

## 8. Candidate dependency

Independent of the survey: [`prompt-security-utils`](https://github.com/andmarios/prompt-security-utils)
(PyPI 1.4.0) implements the injection-wrapping pattern described in §5 — generated markers,
field and payload wrapping, suspicious-content detection, and instruction text for the model.
Worth evaluating on its merits as a dependency, against implementing the same pattern directly.
Named here because it is a package we might depend on, which is a citation rather than a review.
