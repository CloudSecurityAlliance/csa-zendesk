# Operational resources

Nothing is hosted. The server is a local stdio process launched by an MCP client — measured:
24 source files, **no listener** (no `uvicorn`, no `bind()`, no `socket.socket`), and
`SECURITY-RESOURCES.md`'s inventory records the same thing as an explicit finding rather than an
omission.

So there is no uptime, no rota and nothing to page. The resources below are **dependencies whose
change breaks this project**, which is the same operational question asked about a different
kind of thing.

| Resource | What depends on it | What its change looks like |
|---|---|---|
| **Zendesk REST API** | Every tool. Egress only | A field or endpoint moves and tools fail at the seam. `check_spec.py` holds the tool count and ADR links honest, but it cannot see upstream |
| **The CSA Zendesk OAuth app** | `authenticate`. Issued per tenant | Revoke it and every install must re-authenticate. The token on disk stops refreshing and the failure surfaces as a refresh error — the same shape as csa-google-workspace#510, where a retired client reported `ready` while every call failed |
| **PyPI + Trusted Publishing (OIDC)** | Installation. No long-lived token, attestations required | The supply-chain surface. `SECURITY-RESOURCES.md` lists it as `public-unauthed` |
| **GitHub + branch protection** | That `main` means something | This repo is public by policy, so a mistake is published rather than merely committed |
| **`tenant-config/private-terms.txt`** | The **literal** half of `check_public_safe.py` | Detailed below — this is the row worth reading |

## The literal half of the public-safety guard runs on one machine

`check_public_safe.py` has two tiers by design. Structural patterns — shapes, naming no
organisation — live in the script. Literal terms live in `tenant-config/private-terms.txt`,
which is **gitignored**, because an earlier version hardcoded the tenant's own terms and thereby
*"became a compact, searchable index of precisely what it existed to hide: the denylist became
the disclosure."*

That design is right and is not the point here. The point is its operational consequence, which
is written nowhere else: **the literal tier runs only on a machine that holds that file.** CI
never references it and cannot, so every CI run reports:

```
coverage: STRUCTURAL ONLY - tenant-config/private-terms.txt not present,
so tenant-specific literals were NOT checked
```

The script is honest about this — it is *"a deliberately reduced-coverage PASS, not a failure"*,
and saying so rather than reporting a clean bill of health it cannot support is exactly right.
But it means **half of this guard depends on one person's laptop having one gitignored file**,
and if that file is lost the guard keeps passing and nobody is told it got weaker. It is not
backed up (see [`BACKUP-RESOURCES.md`](BACKUP-RESOURCES.md) — it should not be committed), and
there is no second copy by design.

Worth knowing before trusting a green `check_public_safe` on a machine other than the
maintainer's.

## Recurring work

Reactive, and there is no schedule. Two standing triggers:

1. **Zendesk ships an API change.** Nothing watches upstream here —
   CINO-Platform-Engineering#49 is the fleet-level version of that gap, and `csa-skilljar` is
   the only server with a drift detector.
2. **A decision this repo cites by number is superseded.** `DECISIONS-INHERITED.md` and
   `DECISIONS-ADR.md` both reference fleet decisions; a superseded one leaves a citation
   asserting a standard that no longer holds.

## Not here

Capacity, alerting, dashboards, on-call, cost. There is nothing running. The token on each
operator's machine is the only state, and it belongs to them —
[`BACKUP-RESOURCES.md`](BACKUP-RESOURCES.md).
