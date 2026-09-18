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
import os
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
# ticket.purge, people.purge, people.merge and people.suspend used to live here.
# analysis/scope-triage-exceptions.csv refuses those operations outright, so no
# tool backs them and a capability for them would be a promise the code does not
# keep. Re-adding one means re-admitting the operation first, deliberately.

PEOPLE_READ = "people.read"
PEOPLE_WRITE = "people.write"
PEOPLE_DELETE = "people.delete"

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
    PEOPLE_READ,
    PEOPLE_WRITE,
    PEOPLE_DELETE,
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
# `default` is everything that can be undone. Reply, solve, delete, bulk and the
# escape hatch are all opt-in - and close and raw are granted by NO profile, so
# enabling them is a deliberate act.
PROFILES: dict[str, frozenset[str]] = {
    "readonly": frozenset({TICKET_READ, HC_READ, PEOPLE_READ, REPORTING_READ, ADMIN_READ}),
    "default": frozenset({TICKET_READ, TICKET_NOTE, TICKET_WRITE, HC_READ, PEOPLE_READ, REPORTING_READ, ADMIN_READ}),
    "agent": frozenset(
        {
            TICKET_READ,
            TICKET_NOTE,
            TICKET_WRITE,
            TICKET_SOLVE,
            HC_READ,
            PEOPLE_READ,
            REPORTING_READ,
            # A form's required fields vary per ticket_form_id (CLAUDE.md: "context is
            # always registered"), and GET /api/v2/ticket_forms{,/…} is classified
            # admin.read in analysis/operation-classification.csv - so the one profile
            # that can solve must also be able to discover what solving requires.
            ADMIN_READ,
        }
    ),
    "editor": frozenset({HC_READ, HC_WRITE, TICKET_READ, PEOPLE_READ, ADMIN_READ}),
    "analyst": frozenset({TICKET_READ, PEOPLE_READ, HC_READ, REPORTING_READ, REPORTING_EXPORT, ADMIN_READ}),
    # `full` is every capability that exists, minus the ones no word should grant.
    # Ordered by REACH first and destructiveness second (DEC-015): ticket.reply is
    # excluded although it destroys nothing, because its effect leaves the building.
    # Reach additionally requires CSA_ZD_ALLOW_REACH - a profile cannot grant it.
    "full": frozenset(ALL_CAPABILITIES) - {TICKET_REPLY, TICKET_CLOSE, RAW_READ, RAW_WRITE},
}

#: Capabilities whose effect leaves the building and touches a person (DEC-015).
#: These need the operator switch IN ADDITION to the capability - holding
#: `ticket.reply` is necessary and not sufficient.
REACH_CAPABILITIES: frozenset[str] = frozenset({TICKET_REPLY})


def reach_permitted() -> bool:
    """Whether outward-facing calls are allowed at all. Off unless explicitly on."""
    return os.environ.get("CSA_ZD_ALLOW_REACH", "").strip().lower() == "true"


def assert_reach_permitted(tool: str) -> None:
    if not reach_permitted():
        raise exc.PolicyError(
            f"`{tool}` sends something to a person outside this organisation, and outward-facing "
            f"calls are off. Set CSA_ZD_ALLOW_REACH=true to enable them. This is deliberately "
            f"separate from the capability profile: a public reply cannot be unsent, so granting "
            f"the capability is necessary and not sufficient."
        )


#: A gate is a constant capability, `None` for an ungated read, or a function of
#: the call's kwargs returning every capability that call requires. The third form
#: is what lets one `PUT` carry several authorities (ADR-003).
Gate = str | None | Callable[[dict[str, Any]], frozenset[str]]

#: Every Backend method needs an entry. Missing means REFUSED.
_GATES: dict[str, Gate] = {
    "get_ticket": TICKET_READ,
}


def _required(name: str, gate: Gate, kwargs: dict[str, Any]) -> frozenset[str]:
    """Every capability `name`'s gate demands for this call.

    A callable gate's return is validated, not trusted. There is no callable
    gate yet - `update_ticket` is the first, per ADR-003, needing `ticket.write`
    plus `ticket.solve` when solving - so this is dead code today and cheap to
    get right before it isn't. Left unvalidated, a gate bug that returns a bare
    string would silently explode through `frozenset(str)` into its individual
    characters (`{'t', 'i', 'c', 'k', 'e', 't'}` for `"ticket"`), producing a
    required-capability set no policy could ever satisfy - failing closed, but
    for a reason nobody could diagnose from the error alone.

    A gate that *raises* is deliberately left to propagate rather than being
    wrapped in `PolicyError`: there is no callable gate that isn't our own
    code, so a raising gate is a bug in csa-zendesk, not a hostile input, and
    wrapping it would misrepresent a crash in our own logic as a considered
    policy refusal - discarding the real traceback in the process.
    """
    if gate is None:
        return frozenset()
    if isinstance(gate, str):
        return frozenset({gate})
    result = gate(kwargs)
    if isinstance(result, (frozenset, set)) and all(isinstance(c, str) for c in result):
        return frozenset(result)
    raise exc.PolicyError(
        f"the gate for `{name}` returned {result!r}, not a set of capability strings. "
        f"This is a programming error in csa-zendesk, not a configuration problem: fix "
        f"the gate function in policy._GATES."
    )


class Policy:
    """An immutable set of granted capabilities.

    Immutable against ordinary use, not against deliberate bypass. `__setattr__`
    refuses attribute assignment, and `__init__` refuses to run a second time
    against an already-constructed instance - closing `pb.policy.__init__(...)`,
    which would otherwise silently re-grant every capability through nothing
    more exotic than calling a public method twice (`.policy` hands back the
    live object, and without this guard `__init__` writes through
    `object.__setattr__` the same way the constructor legitimately does).

    This is not protection against `object.__setattr__(p, "capabilities", ...)`
    itself, called directly rather than through `__init__` - that always works
    in Python and cannot be prevented from inside the class, the same category
    of residual, unavoidable path as reaching `HttpClient`'s credential through
    `_authorize.__closure__`. Both require the caller to already have arbitrary
    code execution in this process, which is a different threat model from "a
    tool call decided to grant itself `ticket.close`." What this guard closes
    is the route that looks like ordinary use: calling `__init__` again.
    """

    __slots__ = ("capabilities",)
    capabilities: frozenset[str]

    def __init__(self, capabilities: frozenset[str]) -> None:
        if hasattr(self, "capabilities"):
            raise AttributeError("Policy is immutable; construct a new one to change capabilities")
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


def _lookup(pb: PolicyBackend) -> tuple[Backend, Policy]:
    """The wrapped `(backend, policy)` pair for `pb`, or a typed refusal.

    `_state` is keyed by wrapper identity and populated only by `__init__`.
    An instance that reaches here without `__init__` ever having run - the
    practical way is unpickling, which reconstructs an instance via `__new__`
    and never calls `__init__` at all - has no entry, and this already fails
    closed: nothing is delegated, no capability is granted (verified live).
    But left as a bare dict lookup it fails with a raw `KeyError`, which is
    the same defect class as an incomplete embedder `Backend` raising a raw
    `AttributeError` from inside this security layer - so it is refused here
    the same way, with a typed error naming the remedy.
    """
    try:
        return _state[pb]
    except KeyError:
        raise exc.PolicyError(
            "this PolicyBackend has no wrapped backend or policy to consult - most "
            "likely it was unpickled, which reconstructs an instance without ever "
            "calling __init__. Construct a new one instead: PolicyBackend(backend, policy)."
        ) from None


def _dispatch(pb: PolicyBackend, name: str, kwargs: dict[str, Any]) -> Any:
    """The one gating implementation, shared by every materialised method.

    Looks up the wrapped backend and policy for `pb`, checks `name`'s gate
    against the call's kwargs, and either refuses (logging method + missing
    capability, never the arguments - they may carry ticket content) or
    delegates to the real backend method.

    The capability check happens before the `hasattr` check below on purpose:
    a caller without the capability gets that refusal regardless of whether
    the backend actually implements the method, since the capability refusal
    is the one that matters for authority, not implementation completeness.
    """
    backend, policy = _lookup(pb)
    gate = _GATES[name]
    absent = policy.missing(_required(name, gate, kwargs))
    if absent:
        log.warning("refused %s: missing %s", name, ", ".join(sorted(absent)))
        raise exc.PolicyError(
            f"`{name}` needs {', '.join(sorted(absent))}, which this install does "
            f"not grant. The capability must be granted in the server's own "
            f"configuration; it cannot be changed from here."
        )
    if not hasattr(backend, name):
        # Independent of _GATES drift (which the cross-check in test_policy.py
        # already forbids): _GATES is ours, but the wrapped instance is an
        # embedder's. Backend is a structural Protocol, so a partial
        # implementation is legitimate Python that type-checks fine and still
        # blows up here at the one call it's missing - without this check, as
        # a raw AttributeError escaping from inside the security layer.
        log.error("backend %s has no %s method despite a declared gate", type(backend).__name__, name)
        raise exc.PolicyError(
            f"`{name}` is declared in policy._GATES but the wrapped backend "
            f"({type(backend).__name__!r}) has no such method. This is a bug in the "
            f"backend implementation, not a policy refusal: implement `{name}` on "
            f"{type(backend).__name__}."
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

    Construct-once, the same way and for the same reason as `Policy`:
    `__init__` refuses to run a second time against an already-constructed
    instance, because it is an ordinary method that writes through
    `_state[self] = ...` - a channel the frozen `__setattr__` below cannot
    see. Without this guard, `pb.__init__(other_backend, Policy(ALL_CAPABILITIES))`
    would silently swap both the wrapped backend and the policy on a live
    wrapper, reaching every capability including `ticket.close` with no
    restart and no operator - calling nothing more exotic than a public
    method twice. This is not protection against code that already has
    execution reaching into module scope and writing `_state[pb] = ...`
    directly - that always works and cannot be prevented, the same category
    of residual, unavoidable path as `object.__setattr__` on `Policy` and
    `HttpClient`'s credential via `_authorize.__closure__`. What this closes
    is the route that looks like ordinary use: calling `__init__` again.
    """

    def __init__(self, backend: Backend, policy: Policy) -> None:
        if self in _state:
            raise AttributeError("PolicyBackend is immutable; construct a new one")
        _state[self] = (backend, policy)

    def __setattr__(self, name: str, value: Any) -> None:
        # The policy cannot be widened from inside. Rebuild the wrapper instead.
        raise AttributeError("PolicyBackend is immutable; construct a new one")

    @property
    def policy(self) -> Policy:
        return _lookup(self)[1]

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
