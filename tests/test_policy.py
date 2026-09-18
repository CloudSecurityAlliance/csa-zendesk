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
    # Generic on purpose: no code reads CSA_ZENDESK_PROFILE or CSA_ZENDESK_CAPABILITIES
    # today, so naming either would advertise a remedy that does not exist yet.
    assert "cannot be changed from here" in msg


def test_the_default_profile_holds_only_reversible_capabilities():
    default = pol.PROFILES["default"]
    for irreversible in (
        pol.TICKET_REPLY,
        pol.TICKET_SOLVE,
        pol.TICKET_CLOSE,
        pol.RAW_READ,
        pol.RAW_WRITE,
        pol.BULK,
    ):
        assert irreversible not in default, irreversible


def test_no_profile_grants_close_or_raw():
    never = {pol.TICKET_CLOSE, pol.RAW_READ, pol.RAW_WRITE}
    for name, caps in pol.PROFILES.items():
        assert not (caps & never), f"profile {name!r} grants {sorted(caps & never)}"


def test_a_profile_granting_solve_can_also_discover_what_solving_requires():
    # A ticket form's required fields vary per ticket_form_id, and reading a
    # form (GET /api/v2/ticket_forms{,/…}) is classified admin.read in
    # analysis/operation-classification.csv - so a profile that can solve but
    # cannot read the form it is solving against is guessing at a requirement
    # it is about to violate. This is a property over every profile, not a
    # restatement of any one profile's contents: it would still catch a future
    # profile that grants ticket.solve without also granting admin.read.
    for name, caps in pol.PROFILES.items():
        if pol.TICKET_SOLVE in caps:
            assert pol.ADMIN_READ in caps, f"profile {name!r} grants ticket.solve but not admin.read"


def test_an_unknown_profile_is_a_loud_error_listing_the_real_ones():
    with pytest.raises(ValueError, match="unknown profile"):
        pol.Policy.from_profile("nope")


def test_a_callable_gates_kwargs_reach_it_through_the_wrapper(monkeypatch):
    # ADR-003: one PUT, several authorities - update_ticket itself arrives in
    # Block 2, but the wiring this depends on must be proven now. A test that
    # only calls `gate(...)` directly (as an earlier version of this test did)
    # asserts nothing about PolicyBackend: it would still pass with _dispatch
    # deleted, since nothing routes the call's actual kwargs through a gate.
    # This installs a real callable gate under policy._GATES, materialises a
    # real gated method the same way _materialise_gated_methods() does for
    # every production entry, and calls it through PolicyBackend - so what is
    # asserted is that a call's kwargs reach its gate, not that a lambda
    # computes what it was written to compute.
    def gate(kw: dict) -> frozenset:
        return frozenset({pol.TICKET_WRITE}) | (
            frozenset({pol.TICKET_SOLVE}) if kw.get("status") == "solved" else frozenset()
        )

    monkeypatch.setitem(pol._GATES, "fake_update_ticket", gate)
    monkeypatch.setattr(pol.PolicyBackend, "fake_update_ticket", pol._make_gated("fake_update_ticket"), raising=False)

    class BackendWithFakeUpdate(FakeBackend):
        def fake_update_ticket(self, **kwargs):
            return {"ok": True, **kwargs}

    write_only = pol.Policy(frozenset({pol.TICKET_WRITE}))
    pb = pol.PolicyBackend(BackendWithFakeUpdate(), write_only)

    # Only ticket.write is needed for a plain field edit, and write_only grants it.
    assert pb.fake_update_ticket(priority="high") == {"ok": True, "priority": "high"}

    # Solving needs ticket.write AND ticket.solve - write_only grants only the
    # first, so this specific kwarg must be refused even though the same policy
    # just permitted a different call to the very same method.
    with pytest.raises(exc.PolicyError, match=pol.TICKET_SOLVE):
        pb.fake_update_ticket(status="solved")


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
    # Filters on "is an upper-case module-level string constant", not on
    # `"." in v`: BULK = "bulk" carries no dot despite being a real capability
    # constant correctly listed in ALL_CAPABILITIES, so a dot-based filter would
    # structurally exclude it and could never pass against a correct policy.py.
    consts = {v for k, v in vars(pol).items() if k.isupper() and isinstance(v, str)}
    assert consts == set(pol.ALL_CAPABILITIES)


# --- additional coverage: branches not reached by the tests above ------------


def test_required_treats_a_none_gate_as_an_ungated_read():
    assert pol._required("get_ticket", None, {"anything": 1}) == frozenset()


def test_required_wraps_a_string_gate_as_a_singleton_set():
    assert pol._required("get_ticket", pol.TICKET_READ, {}) == frozenset({pol.TICKET_READ})


def test_required_delegates_a_callable_gate_to_the_kwargs():
    def gate(kw: dict) -> frozenset:
        return frozenset({pol.TICKET_WRITE, pol.TICKET_SOLVE})

    assert pol._required("get_ticket", gate, {"status": "solved"}) == {pol.TICKET_WRITE, pol.TICKET_SOLVE}


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


def test_the_policy_wrapper_is_itself_a_backend():
    # Property 2: an embedder holding a PolicyBackend has what an MCP client has.
    # This must hold on every supported Python: 3.12 changed protocol isinstance to
    # use inspect.getattr_static(), which does not consult __getattr__, so a
    # dynamically-provided method satisfies hasattr and fails isinstance.
    pb = pol.PolicyBackend(FakeBackend(), pol.Policy.from_profile("default"))
    assert isinstance(pb, Backend)
    assert inspect.getattr_static(pb, "get_ticket") is not None


def test_the_wrapper_exposes_exactly_the_gated_methods():
    # The generated surface and the gate table cannot drift apart.
    exposed = {n for n in dir(pol.PolicyBackend) if not n.startswith("_") and n != "policy"}
    assert exposed == set(pol._GATES), f"exposed {sorted(exposed)} != gates {sorted(pol._GATES)}"


def test_every_gate_names_a_real_backend_method_and_every_method_has_a_gate():
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


# --- construct-once: closing in-band widening via __init__ re-invocation -----


def test_reinvoking_init_on_a_live_policy_is_refused_and_leaves_it_unchanged():
    # pb.policy.__init__(...) is calling an ordinary public method a second
    # time, not an exotic bypass. A guard that raises after already having
    # mutated capabilities would be worse than no guard, so both are
    # asserted: the raise, and that capabilities are the ORIGINAL ones after it.
    p = pol.Policy(frozenset({pol.TICKET_READ}))
    with pytest.raises(AttributeError):
        p.__init__(frozenset(pol.ALL_CAPABILITIES))  # type: ignore[misc]
    assert p.capabilities == frozenset({pol.TICKET_READ})


def test_widening_through_policy_dunder_init_is_refused_end_to_end():
    # The end-to-end reproduction: pb.policy hands back the live Policy, and
    # without the reinvocation guard this call silently re-granted every
    # capability with no restart and no operator. Both the widening attempt
    # and the subsequent call must still be refused.
    pb = pol.PolicyBackend(FakeBackend(), pol.Policy(frozenset()))
    with pytest.raises(AttributeError):
        pb.policy.__init__(frozenset(pol.ALL_CAPABILITIES))  # type: ignore[misc]
    with pytest.raises(exc.PolicyError):
        pb.get_ticket(ticket_id=1)


def test_reinvoking_init_on_a_live_policybackend_is_refused_and_leaves_it_unchanged():
    # The same defect one level up: PolicyBackend.__init__ writes
    # _state[self] = (backend, policy) with no guard against a second call,
    # which would silently swap BOTH the wrapped backend and the policy on a
    # live wrapper - reaching every capability with no restart and no operator,
    # by calling nothing more exotic than a public method twice. Both the raise
    # and that the original backend/policy are unchanged afterwards are asserted.
    original_backend = FakeBackend({1: {"id": 1}})
    original_policy = pol.Policy(frozenset({pol.TICKET_READ}))
    pb = pol.PolicyBackend(original_backend, original_policy)
    with pytest.raises(AttributeError):
        pb.__init__(FakeBackend({2: {"id": 2}}), pol.Policy(frozenset(pol.ALL_CAPABILITIES)))  # type: ignore[misc]
    assert pb.policy is original_policy
    assert pol._state[pb][0] is original_backend


def test_widening_through_policybackend_dunder_init_is_refused_end_to_end():
    # The wrapper-level mirror of the Policy end-to-end test: attempting to
    # swap in a fully-permissive policy (and a different backend) through
    # pb.__init__(...) must raise, and the subsequent call must still be
    # refused by the ORIGINAL empty policy, not silently served by the new one.
    pb = pol.PolicyBackend(FakeBackend({1: {"id": 1}}), pol.Policy(frozenset()))
    with pytest.raises(exc.PolicyError):
        pb.get_ticket(ticket_id=1)
    with pytest.raises(AttributeError):
        pb.__init__(FakeBackend({1: {"id": 1}}), pol.Policy(frozenset(pol.ALL_CAPABILITIES)))  # type: ignore[misc]
    with pytest.raises(exc.PolicyError):
        pb.get_ticket(ticket_id=1)


@pytest.mark.parametrize(
    "build_live_instance",
    [
        lambda: pol.Policy(frozenset({pol.TICKET_READ})),
        lambda: pol.PolicyBackend(FakeBackend({1: {"id": 1}}), pol.Policy(frozenset({pol.TICKET_READ}))),
    ],
    ids=["Policy", "PolicyBackend"],
)
def test_no_construct_once_object_can_be_reinitialised(build_live_instance):
    # Policy and PolicyBackend are both construct-once for the same reason:
    # __init__ is an ordinary method that writes through a channel their frozen
    # __setattr__ cannot see. One guard covering every class with this property,
    # parametrized, so it is still there when a third one is added - rather than
    # a second copy-pasted, class-specific test that a fix to one class's
    # __init__ guard could leave the other class uncovered by.
    instance = build_live_instance()
    with pytest.raises(AttributeError):
        instance.__init__(*_widened_init_args(instance))


def _widened_init_args(instance: object) -> tuple:
    if isinstance(instance, pol.Policy):
        return (frozenset(pol.ALL_CAPABILITIES),)
    return (FakeBackend({1: {"id": 1}}), pol.Policy(frozenset(pol.ALL_CAPABILITIES)))


def test_a_callable_gate_returning_a_bare_string_is_refused_not_exploded():
    # A gate bug that returns "ticket" instead of {"ticket"} must not silently
    # explode through frozenset(str) into {'t','i','c','k','e','t'} - a required
    # set no policy could ever satisfy, failing closed for a reason nobody
    # could diagnose from the error alone.
    def gate(kw: dict) -> frozenset:
        return "ticket.read"  # type: ignore[return-value]

    with pytest.raises(exc.PolicyError, match="not a set of capability strings"):
        pol._required("get_ticket", gate, {})


def test_a_callable_gate_that_raises_propagates_unwrapped():
    # A raising gate is a bug in OUR code (there is no callable gate that
    # isn't ours), not hostile input, so it propagates rather than being
    # laundered into a PolicyError that would misrepresent a crash as a
    # considered policy refusal.
    def gate(kw: dict) -> frozenset:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        pol._required("get_ticket", gate, {})


def test_an_incomplete_embedder_backend_raises_a_typed_error_not_a_raw_attributeerror():
    # Backend is a structural Protocol, so an embedder's partial implementation
    # - missing a method _GATES declares - is legitimate Python that type-checks
    # fine. This is independent of _GATES drift (_GATES is ours; the instance
    # is an embedder's), so it needs nothing the drift cross-check forbids:
    # just a class missing a method.
    class IncompleteBackend:
        pass  # no get_ticket at all

    pb = pol.PolicyBackend(IncompleteBackend(), pol.Policy.from_profile("full"))
    with pytest.raises(exc.PolicyError, match="no such method"):
        pb.get_ticket(ticket_id=1)


def test_an_unpickled_policybackend_refuses_gated_calls_with_a_typed_error():
    # Unpickling reconstructs an instance via __new__ and never calls __init__,
    # so the unpickled wrapper has no entry in _state. That already fails
    # closed (verified: nothing delegated, no capability granted) - this
    # asserts it fails as a typed PolicyError, not a raw KeyError escaping
    # from inside the security layer.
    #
    # pickle.loads here round-trips an object this same test just built
    # in-process; nothing crosses a trust boundary.
    import pickle

    pb = pol.PolicyBackend(FakeBackend({1: {"id": 1}}), pol.Policy.from_profile("full"))
    unpickled = pickle.loads(pickle.dumps(pb))
    with pytest.raises(exc.PolicyError, match="unpickled"):
        unpickled.get_ticket(ticket_id=1)


def test_an_unpickled_policybackends_policy_property_is_also_a_typed_refusal():
    # The same defect, same fix, at the second (and only other) call site that
    # reads _state directly: the .policy property. Same in-process round-trip,
    # nothing crosses a trust boundary.
    import pickle

    pb = pol.PolicyBackend(FakeBackend(), pol.Policy.from_profile("full"))
    unpickled = pickle.loads(pickle.dumps(pb))
    with pytest.raises(exc.PolicyError, match="unpickled"):
        unpickled.policy  # noqa: B018


def test_the_refused_operations_have_no_capability_at_all():
    # analysis/scope-triage-exceptions.csv refuses purge, the two merges and
    # mark_as_spam outright. They must not be grantable.
    for gone in ("ticket.purge", "people.purge", "people.merge", "people.suspend"):
        assert gone not in pol.ALL_CAPABILITIES


def test_no_profile_grants_a_reach_capability():
    # DEC-015: reach carries an operator switch SEPARATE from the capability
    # profile. A profile that grants ticket.reply makes the switch decorative.
    for name, caps in pol.PROFILES.items():
        assert pol.TICKET_REPLY not in caps, f"profile {name!r} grants reach"
