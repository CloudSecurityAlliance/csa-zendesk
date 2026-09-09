import inspect

import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk import policy as pol
from csa_zendesk.backend import Backend, FakeBackend


def wrapped(profile="default", tickets=None):
    return pol.PolicyBackend(FakeBackend(tickets=tickets or {1: {"id": 1}}), pol.Policy.from_profile(profile))


def test_every_backend_method_has_a_declared_gate():
    # THE fail-closed guard. A method added to the Protocol without a gate must
    # fail CI here, not silently ship ungoverned.
    declared = {n for n in dir(Backend) if not n.startswith("_")}
    assert declared <= set(pol._GATES), f"undeclared: {declared - set(pol._GATES)}"


def test_a_method_with_no_gate_entry_is_refused_not_delegated():
    class Extra(FakeBackend):
        def undeclared_method(self, *, x: int) -> dict:  # pragma: no cover
            return {"x": x}

    pb = pol.PolicyBackend(Extra(), pol.Policy.from_profile("full"))
    with pytest.raises(exc.PolicyError, match="no declared capability gate"):
        pb.undeclared_method(x=1)


def test_the_default_profile_can_read_a_ticket():
    assert wrapped().get_ticket(ticket_id=1) == {"ticket": {"id": 1}}


def test_a_profile_without_the_capability_is_refused_with_a_remedy():
    pb = pol.PolicyBackend(FakeBackend({1: {"id": 1}}), pol.Policy(frozenset()))
    with pytest.raises(exc.PolicyError) as ei:
        pb.get_ticket(ticket_id=1)
    msg = str(ei.value)
    assert "ticket.read" in msg  # names the missing capability
    assert "CSA_ZENDESK_PROFILE" in msg  # names what an operator changes


def test_the_default_profile_holds_only_reversible_capabilities():
    default = pol.PROFILES["default"]
    for irreversible in (
        pol.TICKET_REPLY,
        pol.TICKET_SOLVE,
        pol.TICKET_CLOSE,
        pol.TICKET_PURGE,
        pol.PEOPLE_MERGE,
        pol.PEOPLE_SUSPEND,
        pol.PEOPLE_PURGE,
        pol.RAW_READ,
        pol.RAW_WRITE,
        pol.BULK,
    ):
        assert irreversible not in default, irreversible


def test_no_profile_grants_purge_close_merge_or_raw():
    never = {pol.TICKET_CLOSE, pol.TICKET_PURGE, pol.PEOPLE_PURGE, pol.PEOPLE_MERGE, pol.RAW_READ, pol.RAW_WRITE}
    for name, caps in pol.PROFILES.items():
        assert not (caps & never), f"profile {name!r} grants {sorted(caps & never)}"


def test_an_unknown_profile_is_a_loud_error_listing_the_real_ones():
    with pytest.raises(ValueError, match="unknown profile"):
        pol.Policy.from_profile("nope")


def test_a_callable_gate_computes_capabilities_from_the_kwargs():
    # ADR-003: one PUT, several authorities. Simulated here because update_ticket
    # itself arrives in Block 2.
    def gate(kw: dict) -> frozenset:
        return frozenset({pol.TICKET_WRITE}) | (
            frozenset({pol.TICKET_SOLVE}) if kw.get("status") == "solved" else frozenset()
        )

    assert gate({"priority": "high"}) == {pol.TICKET_WRITE}
    assert gate({"status": "solved"}) == {pol.TICKET_WRITE, pol.TICKET_SOLVE}


def test_missing_reports_every_absent_capability_not_just_the_first():
    p = pol.Policy(frozenset({pol.TICKET_WRITE}))
    assert p.missing(frozenset({pol.TICKET_WRITE, pol.TICKET_SOLVE, pol.TICKET_CLOSE})) == {
        pol.TICKET_SOLVE,
        pol.TICKET_CLOSE,
    }


def test_bulk_is_additive_never_a_substitute():
    # Granting the power to delete one ticket must not grant deleting a thousand.
    p = pol.Policy(frozenset({pol.TICKET_DELETE}))
    assert p.missing(frozenset({pol.TICKET_DELETE, pol.BULK})) == {pol.BULK}


def test_the_policy_cannot_be_widened_from_inside():
    pb = wrapped()
    with pytest.raises(AttributeError):
        pb._policy = pol.Policy.from_profile("full")  # type: ignore[misc]


def test_private_attributes_are_not_reachable_through_the_wrapper():
    with pytest.raises(AttributeError):
        wrapped()._backend  # noqa: B018


def test_capability_constants_and_the_all_tuple_agree():
    # NOTE: the brief's own version of this test filters on `"." in v`, which
    # structurally excludes BULK = "bulk" (no dot) even though BULK is a real
    # capability constant correctly listed in ALL_CAPABILITIES - a test that
    # could never pass against the brief's own reference implementation. Fixed
    # here to filter on "is an upper-case module-level string constant", which
    # is what the test is actually trying to check and which does include BULK.
    consts = {v for k, v in vars(pol).items() if k.isupper() and isinstance(v, str)}
    assert consts == set(pol.ALL_CAPABILITIES)


# --- additional coverage: branches the brief's own test list does not reach ---


def test_required_treats_a_none_gate_as_an_ungated_read():
    assert pol._required(None, {"anything": 1}) == frozenset()


def test_required_wraps_a_string_gate_as_a_singleton_set():
    assert pol._required(pol.TICKET_READ, {}) == frozenset({pol.TICKET_READ})


def test_required_delegates_a_callable_gate_to_the_kwargs():
    def gate(kw: dict) -> frozenset:
        return frozenset({pol.TICKET_WRITE, pol.TICKET_SOLVE})

    assert pol._required(gate, {"status": "solved"}) == {pol.TICKET_WRITE, pol.TICKET_SOLVE}


def test_the_wrapper_exposes_its_policy():
    policy = pol.Policy.from_profile("agent")
    pb = pol.PolicyBackend(FakeBackend(), policy)
    assert pb.policy is policy


def test_a_policy_cannot_be_widened_by_attribute_assignment():
    p = pol.Policy(frozenset({pol.TICKET_READ}))
    with pytest.raises(AttributeError):
        p.capabilities = frozenset(pol.ALL_CAPABILITIES)  # type: ignore[misc]


def test_policy_repr_lists_its_sorted_capabilities():
    p = pol.Policy(frozenset({pol.TICKET_WRITE, pol.TICKET_READ}))
    assert repr(p) == f"Policy({sorted([pol.TICKET_WRITE, pol.TICKET_READ])!r})"


def test_dunder_attribute_access_on_the_wrapper_is_unaffected():
    # The private-name block targets `_backend`/`_policy`-style guesses; it must
    # not collaterally break normal object machinery.
    pb = wrapped()
    assert pb.__class__ is pol.PolicyBackend


def test_the_policy_wrapper_is_itself_a_backend() -> None:
    # Property 2: an embedder holding a PolicyBackend has what an MCP client has.
    # This must hold on every supported Python: 3.12 changed protocol isinstance to
    # use inspect.getattr_static(), which does not consult __getattr__, so a
    # dynamically-provided method satisfies hasattr and fails isinstance.
    pb = pol.PolicyBackend(FakeBackend(), pol.Policy.from_profile("default"))
    assert isinstance(pb, Backend)
    assert inspect.getattr_static(pb, "get_ticket") is not None


def test_the_wrapper_exposes_exactly_the_gated_methods() -> None:
    # The generated surface and the gate table cannot drift apart.
    exposed = {n for n in dir(pol.PolicyBackend) if not n.startswith("_") and n != "policy"}
    assert exposed == set(pol._GATES), f"exposed {sorted(exposed)} != gates {sorted(pol._GATES)}"


def test_every_gate_names_a_real_backend_method_and_every_method_has_a_gate() -> None:
    # backend.py's docstring promises adding a Backend method obliges three things: an
    # ApiBackend implementation, a FakeBackend implementation, and a _GATES entry.
    # tests/test_backend.py enforces the first two against each other. This enforces the
    # third - and it must compare _GATES against the PROTOCOL, not against itself. A
    # guard that checks `exposed == set(_GATES)` passes happily when both sides carry the
    # same bogus name, which is exactly how a stray gate once shipped green.
    protocol = {n for n in dir(Backend) if not n.startswith("_") and callable(getattr(Backend, n, None))}
    gates = set(pol._GATES)
    assert gates, "gate guard has gone vacuous - _GATES is empty"
    assert gates == protocol, (
        f"gate table and Backend disagree - gates for no such method: "
        f"{sorted(gates - protocol)} (delete them, or add the method); "
        f"methods with no gate: {sorted(protocol - gates)} "
        f"(add a _GATES entry - they are refused until you do)"
    )


def test_reinvoking_init_on_a_live_policy_is_refused_and_leaves_it_unchanged() -> None:
    # CRITICAL: pb.policy.__init__(...) is calling an ordinary public method a
    # second time, not an exotic bypass. A guard that raises after already
    # having mutated capabilities would be worse than no guard, so both are
    # asserted: the raise, and that capabilities are the ORIGINAL ones after it.
    p = pol.Policy(frozenset({pol.TICKET_READ}))
    with pytest.raises(AttributeError):
        p.__init__(frozenset(pol.ALL_CAPABILITIES))  # type: ignore[misc]
    assert p.capabilities == frozenset({pol.TICKET_READ})


def test_widening_through_policy_dunder_init_is_refused_end_to_end() -> None:
    # The end-to-end reproduction: pb.policy hands back the live Policy, and
    # without the reinvocation guard this call silently re-granted every
    # capability with no restart and no operator. Both the widening attempt
    # and the subsequent call must still be refused.
    pb = pol.PolicyBackend(FakeBackend(), pol.Policy(frozenset()))
    with pytest.raises(AttributeError):
        pb.policy.__init__(frozenset(pol.ALL_CAPABILITIES))  # type: ignore[misc]
    with pytest.raises(exc.PolicyError):
        pb.get_ticket(ticket_id=1)
