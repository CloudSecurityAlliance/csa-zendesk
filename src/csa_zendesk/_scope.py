"""Allowlists: which objects may be acted on at all.

The third operator control (whole-project design §1). Toolsets select surface,
capabilities grant authority, allowlists select SUBJECT - and all three are
enforced at the same seam so a library embedder gets the same refusal an MCP
client does.

**This is a blast-radius control, not a security boundary.** The server acts as
the operating user, so nothing is reachable here that is not already reachable
in Zendesk. It protects against the agent doing the wrong thing; it does not
protect against a user exceeding their authority, and for an administrator it
constrains almost nothing. Anyone who grants this tool a wider credential
believing the allowlist will hold it has misread it.

Shape adopted from csa-google-workspace's allowlist.py: configured in the
environment so the policy lives where the operator declares the server and can
see it; three outcomes, where unusable means NOTHING permitted; and `*` distinct
from empty, because one is a deliberate decision and the other is a typo.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import exceptions as exc

_ID = re.compile(r"^[0-9]+$")


class AllowlistError(exc.ZendeskError):
    """The allowlist is configured but unusable. Never degrades to 'no restrictions'."""


@dataclass(frozen=True, slots=True)
class Listing:
    all_subjects: bool
    ids: frozenset[str] = frozenset()


def read_listing(var: str) -> Listing:
    raw = os.environ.get(var, "")
    if raw.strip() == "*":
        return Listing(all_subjects=True)
    ids: set[str] = set()
    for line in raw.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", line).strip()
        for piece in (p.strip() for p in line.split(",")):
            if not piece:
                continue
            if not _ID.match(piece):
                raise AllowlistError(
                    f"{var}: {piece!r} is not a numeric id. Entries are Zendesk ids, one per line "
                    f"or comma-separated, with `#` starting a comment. Nothing is permitted while "
                    f"this value is unusable."
                )
            ids.add(piece)
    return Listing(all_subjects=False, ids=frozenset(ids))


def permits(listing: Listing, subject: str) -> bool:
    return listing.all_subjects or subject in listing.ids
