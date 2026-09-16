# Upstream OpenAPI snapshots

Fetched 2026-08-30 from developer.zendesk.com. These are snapshots of
someone else's moving target; re-fetch and diff before trusting them.

Zendesk publishes these three documents but **links to none of them** — all three
were found by probing URL shapes, not from the API reference. There is no upstream
version stamp, no changelog and no `ETag` discipline to lean on: the only way to
know whether a snapshot is current is to fetch the URL again and diff it. The
`info.version` field does not move when the content does (see the drift log below),
so it is not a currency signal.

| file | title | info.version | source URL | sha256 | bytes |
|---|---|---|---|---|---|
| `zendesk-support-oas.yaml` | Support API | 2.0.0 | https://developer.zendesk.com/zendesk/oas.yaml | `7b5af44992f97196…` | 1802296 |
| `zendesk-help-center-oas.yaml` | Help Center API | 2.0.0 | https://developer.zendesk.com/help_center/oas.yaml | `af3cfdda4daec817…` | 311383 |
| `zendesk-voice-oas.yaml` | Talk API | 2022-04-04 | https://developer.zendesk.com/voice/oas.yaml | `470e2489af4c0cb4…` | 202564 |

Full digests:

```
7b5af44992f97196d083885b1536eb62d40cc3090f13210744b61868561ce476  zendesk-support-oas.yaml
af3cfdda4daec81757b2b5a9f8e06bbc1153f734575887ebafb806b19db23ba6  zendesk-help-center-oas.yaml
470e2489af4c0cb4089cf849d542b16a1cb617e799740be5b3a08673a7586e24  zendesk-voice-oas.yaml
```

Operation counts as snapshotted, which is what `analysis/operation-inventory.csv`
and the README scope table are derived from:

| file | paths | operations | reads (GET) | mutating (POST/PUT/PATCH/DELETE) |
|---|---:|---:|---:|---:|
| `zendesk-support-oas.yaml` | 444 | 640 | 333 | 307 |
| `zendesk-help-center-oas.yaml` | 120 | 182 | 96 | 86 |
| `zendesk-voice-oas.yaml` | 37 | 60 | 31 | 29 |
| **total** | **601** | **882** | **460** | **422** |

Nearly half the published surface mutates. That is the number the capability
policy has to be sized against, not 882.

## Re-verifying

```bash
cd specs && shasum -a 256 *.yaml && wc -c *.yaml
curl -sS -o /tmp/support.yaml https://developer.zendesk.com/zendesk/oas.yaml
diff -u zendesk-support-oas.yaml /tmp/support.yaml
```

The snapshots are **pinned deliberately**. `analysis/operation-inventory.csv`, the
family probes and the README scope table are all generated from the bytes in this
directory, so refreshing a spec is not a file copy — it invalidates the inventory
and every count downstream of it. Refresh and regenerate together, in one change.

## Drift log

### 2026-09-16 — re-fetched, all three URLs reachable (HTTP 200), all three drifted

The snapshots are **stale but not wrong**: nothing was removed or renamed, so no
existing inventory row is invalidated. What is missing is new surface.

**`zendesk-support-oas.yaml`** — 1802296 → 1849452 bytes, 640 → 649 operations
(444 → 449 paths). Nine new operations, none removed:

| method | path | note |
|---|---|---|
| GET | `/api/v2/users/{user_id}/suspension` | new Users sub-family: per-channel suspension records |
| POST | `/api/v2/users/{user_id}/suspension` | |
| PUT | `/api/v2/users/{user_id}/suspension` | |
| DELETE | `/api/v2/users/{user_id}/suspension` | |
| GET | `/api/v2/tickets/autocomplete` | subject-prefix ticket search |
| GET | `/api/v2/tickets/{ticket_id}/metric_events` | per-ticket metric events |
| GET | `/api/v2/tickets/{ticket_id}/slas/policy_metrics` | Enterprise plan |
| GET | `/api/v2/tickets/{ticket_id}/group_slas/policy_metrics` | Group SLA Policies — an [ADR-001](../DECISIONS-ADR/ADR-001.md) excluded family |
| PUT | `/api/v2/routing/attributes/{attribute_id}/values/{attribute_value_id}` | Skill Based Routing |

28 schemas added, 0 removed, 11 changed. The changes that alter a contract rather
than prose:

- **`GET /api/v2/search/export` now declares `filter[type]` as `required: true`.**
  This is the cursor-paginated escape from the 1000-result search cap (README
  finding 2), so the generator must not emit it as optional.
- **`TicketContentPin` ids change type `string` → `integer`.** A snapshot-driven
  model would have typed these wrong.
- **`PUT /api/v2/tickets/{ticket_id}/comments/{ticket_comment_id}/redact` gains a
  request body** (`{text: string}`, required). The snapshot describes a redaction
  call with no body at all — it could not have been generated correctly.
- `TicketCreateRequest`, `CustomStatusCreateRequest` and the `POST /tickets` and
  `POST /custom_statuses` bodies gain `required: true`; `CustomStatusCreateInput`
  gains three required properties.
- `POST /api/v2/ticket_content_pins` now wraps its response in a
  `{ticket_content_pin: …}` envelope instead of returning the bare object.
- `CustomRoleConfigurationObject` gains ~20 permission flags (masking, IT asset
  management, deletion schedules, IP bans, group/brand ticket access).
- `CustomObjectRecordFilterExpression` loses its self-recursion in favour of five
  explicit `…Level1`–`Level5` schemas — a nesting depth cap made structural.
- Two new tags, **Facebook Channel** and **Unified Agent Statuses**, with schemas
  (`FacebookPagesResponse`, `UserSeatsResponse`, `TokenRequest`/`TokenResponse`,
  `SkillBasedRoutingAgentSearch*`, `UserEntitlementsFullUpdateRequest`) that **no
  operation references**. Upstream is staging surface it has not published yet.

**`zendesk-help-center-oas.yaml`** — 311383 → 311625 bytes. Operation set,
schema set and every operation body are **byte-identical**. The only change is
auth, and it is broken: two security schemes are added (`bearerAuth`,
`sessionAuth`) and the document-level requirement flips from `basicAuth: []` to
**`oauth2: []` — a scheme that is not defined in `securitySchemes`**. The
published document does not validate. Read it as intent (OAuth is where Zendesk
is going, consistent with the 2027-04-30 API-token retirement) rather than as a
spec to generate from.

**`zendesk-voice-oas.yaml`** — 202564 → 203982 bytes. Operation set and schema
set unchanged; 4 operation bodies changed. All documentation and fixture edits:
three new `phone_number` properties documented in a prose table
(`all_groups_access_enabled`, `auto_accept_enabled`, `auto_accept_time`,
`outbound_calls_group_ids` — prose only, not in any schema), added IVR-route
request examples, and rotated example ids. Nothing here changes a contract.
