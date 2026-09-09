"""Capability gating, enforced by a wrapper around the Backend seam.

Two properties are load-bearing (ADR-002, ADR-003, ADR-010):

* **One wrapper.** Enforcement lives here, not in the tools, so a library
  embedder gets the same guarantee an MCP client does.
* **Fail closed.** `_GATES` must name every `Backend` method. An unlisted name is
  REFUSED, not delegated - so a newly added method arrives *off* rather than
  ungoverned, and forgetting a declaration turns a feature off instead of leaving
  a hole. `tests/test_policy.py` fails CI when the two drift.

Capabilities are `<domain>.<tier>`, ordered by whether the action can be undone,
and derived from a classification of all in-scope operations rather than from
one workflow (ADR-010, `analysis/operation-classification.csv`).

`bulk` is CROSS-CUTTING: required *in addition* to the domain capability for any
`_many` / `/bulk` / `/import` operation. Granting the power to delete one ticket
does not grant the power to delete a thousand.

The policy cannot be widened in-band: no method changes it, and the configuration
is the complete permitted list rather than a delta.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable
from typing import Any

from . import exceptions as exc
from .backend import Backend

log = logging.getLogger(__name__)

# --- capabilities, ordered by reversibility within each domain ---------------
TICKET_READ = "ticket.read"
TICKET_NOTE = "ticket.note"  # internal note; never leaves the org
TICKET_WRITE = "ticket.write"  # fields, assignee, tags; audited
TICKET_REPLY = "ticket.reply"  # PUBLIC comment; emailed, irreversible
TICKET_SOLVE = "ticket.solve"  # on-ramp to terminal: automation closes solved
TICKET_CLOSE = "ticket.close"  # terminal immediately; also covers merge
TICKET_DELETE = "ticket.delete"  # soft delete; recoverable with effort
TICKET_PURGE = "ticket.purge"  # "Delete Ticket Permanently"

PEOPLE_READ = "people.read"
PEOPLE_WRITE = "people.write"
PEOPLE_SUSPEND = "people.suspend"  # mark-as-spam suspends the REQUESTER
PEOPLE_MERGE = "people.merge"  # irreversible identity merge
PEOPLE_DELETE = "people.delete"
PEOPLE_PURGE = "people.purge"  # "Permanently Delete User"

HC_READ = "hc.read"
HC_WRITE = "hc.write"
HC_DELETE = "hc.delete"

# 225 configuration operations - 27% of the surface. Editing a trigger changes
# behaviour for every future ticket, silently. Splitting per object type is
# deferred (TODO B9) because admin is off by default in 1.0.
ADMIN_READ = "admin.read"
ADMIN_WRITE = "admin.write"
ADMIN_DELETE = "admin.delete"

REPORTING_READ = "reporting.read"
REPORTING_EXPORT = "reporting.export"

RAW_READ = "raw.read"  # the escape hatch; no profile grants it
RAW_WRITE = "raw.write"

BULK = "bulk"  # cross-cutting; additive, never a substitute

ALL_CAPABILITIES: tuple[str, ...] = (
    TICKET_READ,
    TICKET_NOTE,
    TICKET_WRITE,
    TICKET_REPLY,
    TICKET_SOLVE,
    TICKET_CLOSE,
    TICKET_DELETE,
    TICKET_PURGE,
    PEOPLE_READ,
    PEOPLE_WRITE,
    PEOPLE_SUSPEND,
    PEOPLE_MERGE,
    PEOPLE_DELETE,
    PEOPLE_PURGE,
    HC_READ,
    HC_WRITE,
    HC_DELETE,
    ADMIN_READ,
    ADMIN_WRITE,
    ADMIN_DELETE,
    REPORTING_READ,
    REPORTING_EXPORT,
    RAW_READ,
    RAW_WRITE,
    BULK,
)

# Named profiles, because nobody composes a capability list correctly under time
# pressure and everybody can pick a word.
#
# `default` is everything that can be undone. Reply, solve, suspend, merge, delete,
# purge, bulk and the escape hatch are all opt-in - and close, purge, merge and raw
# are granted by NO profile, so enabling them is a deliberate act.
PROFILES: dict[str, frozenset[str]] = {
    "readonly": frozenset({TICKET_READ, HC_READ, PEOPLE_READ, REPORTING_READ, ADMIN_READ}),
    "default": frozenset({TICKET_READ, TICKET_NOTE, TICKET_WRITE, HC_READ, PEOPLE_READ, REPORTING_READ, ADMIN_READ}),
    "agent": frozenset(
        {TICKET_READ, TICKET_NOTE, TICKET_WRITE, TICKET_REPLY, TICKET_SOLVE, HC_READ, PEOPLE_READ, REPORTING_READ}
    ),
    "editor": frozenset({HC_READ, HC_WRITE, TICKET_READ, PEOPLE_READ}),
    "analyst": frozenset({TICKET_READ, PEOPLE_READ, HC_READ, REPORTING_READ, REPORTING_EXPORT, ADMIN_READ}),
    # `full` is everything EXCEPT the four nobody should get by naming a word.
    "full": frozenset(ALL_CAPABILITIES) - {TICKET_CLOSE, TICKET_PURGE, PEOPLE_PURGE, PEOPLE_MERGE, RAW_READ, RAW_WRITE},
}

#: A gate is a constant capability, `None` for an ungated read, or a function of
#: the call's kwargs returning every capability that call requires. The third form
#: is what lets one `PUT` carry several authorities (ADR-003).
Gate = str | None | Callable[[dict[str, Any]], frozenset[str]]

#: Every Backend method needs an entry. Missing means REFUSED.
_GATES: dict[str, Gate] = {
    "get_ticket": TICKET_READ,
}


def _required(gate: Gate, kwargs: dict[str, Any]) -> frozenset[str]:
    if gate is None:
        return frozenset()
    if isinstance(gate, str):
        return frozenset({gate})
    return gate(kwargs)


class Policy:
    """An immutable set of granted capabilities."""

    __slots__ = ("capabilities",)
    capabilities: frozenset[str]

    def __init__(self, capabilities: frozenset[str]) -> None:
        object.__setattr__(self, "capabilities", frozenset(capabilities))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a Policy is immutable; construct a new one")

    def __repr__(self) -> str:
        return f"Policy({sorted(self.capabilities)!r})"

    @classmethod
    def from_profile(cls, name: str) -> Policy:
        try:
            return cls(PROFILES[name])
        except KeyError:
            raise ValueError(f"unknown profile {name!r}. Choose one of: {', '.join(sorted(PROFILES))}") from None

    def missing(self, required: frozenset[str]) -> frozenset[str]:
        """Every required capability this policy does not grant. Empty means allowed."""
        return frozenset(required) - self.capabilities


# The wrapped backend and policy live here, keyed by wrapper identity, rather than
# as instance attributes of PolicyBackend - even one written via
# `object.__setattr__` to dodge the frozen `__setattr__` below. Normal attribute
# lookup finds an instance-`__dict__` entry before `__getattr__` is ever
# consulted, so a real `self._backend` would make `pb._backend` return the raw
# backend directly and defeat every gate downstream. Keeping the pair fully out
# of the instance's own namespace means there is nothing there to find by
# guessing a private name - the only way to the backend is through the gate.
# Entries are per-wrapper-instance (keyed by identity, not shared), so two
# `PolicyBackend`s never see each other's backend or policy.
_state: weakref.WeakKeyDictionary[PolicyBackend, tuple[Backend, Policy]] = weakref.WeakKeyDictionary()


def _dispatch(pb: PolicyBackend, name: str, kwargs: dict[str, Any]) -> Any:
    """The one gating implementation, shared by every materialised method.

    Looks up the wrapped backend and policy for `pb`, checks `name`'s gate
    against the call's kwargs, and either refuses (logging method + missing
    capability, never the arguments - they may carry ticket content) or
    delegates to the real backend method.
    """
    backend, policy = _state[pb]
    gate = _GATES[name]
    absent = policy.missing(_required(gate, kwargs))
    if absent:
        log.warning("refused %s: missing %s", name, ", ".join(sorted(absent)))
        raise exc.PolicyError(
            f"`{name}` needs {', '.join(sorted(absent))}, which this install does "
            f"not grant. Set CSA_ZENDESK_PROFILE to a profile that includes it — or "
            f"for a capability no profile grants, list it explicitly in "
            f"CSA_ZENDESK_CAPABILITIES — then restart. The policy cannot be changed "
            f"from here."
        )
    return getattr(backend, name)(**kwargs)


class PolicyBackend:
    """Wraps a Backend and refuses anything the policy does not permit.

    Every name in `_GATES` is materialised onto this class as a real method
    (see the generation loop below the class body), so `PolicyBackend` passes
    `isinstance(pb, Backend)` and `dir(PolicyBackend)` tells the truth - on
    Python 3.12+, `typing.Protocol`'s `isinstance` check uses
    `inspect.getattr_static()`, which does not consult `__getattr__`, so a
    method that existed only dynamically would satisfy `hasattr` but fail
    `isinstance` (and would do so inconsistently across the 3.10-3.13 floor,
    since `getattr_static` is the 3.12 change). `__getattr__` remains the
    fail-closed catch-all for every name absent from `_GATES`: the generation
    loop covers what is declared, `__getattr__` covers what is not, and a
    forgotten `_GATES` entry still turns a method off rather than leaving it
    ungoverned.

    The wrapped backend and policy are never instance attributes: they live in
    the module-level `_state` `WeakKeyDictionary` above, keyed by wrapper
    identity. An instance attribute (even one written via
    `object.__setattr__`) would be found by ordinary attribute lookup before
    `__getattr__` is ever consulted, which would make `pb._backend` return the
    raw, ungated backend directly.
    """

    def __init__(self, backend: Backend, policy: Policy) -> None:
        _state[self] = (backend, policy)

    def __setattr__(self, name: str, value: Any) -> None:
        # The policy cannot be widened from inside. Rebuild the wrapper instead.
        raise AttributeError("PolicyBackend is immutable; construct a new one")

    @property
    def policy(self) -> Policy:
        return _state[self][1]

    def __getattr__(self, name: str) -> Any:
        # Reached only for names NOT materialised below - i.e. anything absent
        # from _GATES (or a private-name guess). THE fail-closed guard: a
        # Backend method with no _GATES entry has no generated method either,
        # so it lands here and is refused rather than silently delegated.
        if name.startswith("_"):
            raise AttributeError(name)
        log.warning("refused %s: no declared capability gate", name)
        raise exc.PolicyError(
            f"`{name}` has no declared capability gate, so it is refused. This is a "
            f"programming error in csa-zendesk, not a configuration problem: add an "
            f"entry to policy._GATES."
        )


def _make_gated(name: str) -> Callable[..., Any]:
    """Build one materialised, gated method bound to `name` in `_GATES`.

    A factory rather than a function defined directly in the loop below: each
    call captures its own `name` as a parameter, so every generated method
    dispatches on the name it was built for rather than on whatever the loop
    variable happens to hold last.
    """

    def gated(self: PolicyBackend, **kwargs: Any) -> Any:
        return _dispatch(self, name, kwargs)

    gated.__name__ = name
    gated.__qualname__ = f"PolicyBackend.{name}"
    gated.__doc__ = f"Gated `{name}`. Refuses unless the policy grants its capability."
    return gated


def _materialise_gated_methods() -> None:
    # A function, not a bare module-level loop: keeps the loop variable out of
    # the module namespace without a trailing `del`, which would raise
    # NameError if _GATES were ever empty (the loop body would never bind it).
    for name in _GATES:
        setattr(PolicyBackend, name, _make_gated(name))


_materialise_gated_methods()
