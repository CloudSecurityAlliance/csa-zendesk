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


def test_the_default_profile_can_search_tickets():
    assert wrapped().search_tickets(query="type:ticket") == {"results": [], "count": 0}


def test_the_default_profile_can_list_comments():
    assert wrapped().list_comments(ticket_id=1) == {"comments": []}


def test_a_policy_without_ticket_read_refuses_list_comments_before_reaching_the_backend():
    class ExplodingComments(FakeBackend):
        def list_comments(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the backend must not be reached")

    pb = pol.PolicyBackend(ExplodingComments(), pol.Policy(frozenset()))
    with pytest.raises(exc.PolicyError, match="ticket.read"):
        pb.list_comments(ticket_id=1)


def test_a_policy_without_ticket_read_refuses_search_before_reaching_the_backend():
    class ExplodingSearch(FakeBackend):
        def search_tickets(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the backend must not be reached")

    pb = pol.PolicyBackend(ExplodingSearch(), pol.Policy(frozenset()))
    with pytest.raises(exc.PolicyError, match="ticket.read"):
        pb.search_tickets(query="x")


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
    never = {pol.TICKET_CLOSE, pol.TICKET_MERGE, pol.RAW_READ, pol.RAW_WRITE}
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
    # Block 1 (this task's own gate is a constant, not yet callable), but the
    # wiring a future callable gate would depend on must be proven now. A test that
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


def test_reach_flagged_tools_and_reach_capable_tools_are_the_same_set():
    # I1: reach enforcement hangs on _GATES, uncross-checked against TOOLS.
    # `_GATES["reply_publicly"] = TICKET_WRITE` (a plausible typo when the ten
    # missing gates land) would silently disarm the reach switch while
    # ToolSpec(reach=True) and the CSV still say contacts-a-person - one
    # hand-maintained list traded for another. This cross-checks the two
    # hand-maintained facts against each other directly, independent of _GATES:
    # every tool ToolSpec flags as reach must carry a capability this module
    # has flagged reach-carrying, and vice versa.
    reach_flagged = {n for n, t in pol.tools.TOOLS.items() if t.reach}
    reach_capable = {n for n, t in pol.tools.TOOLS.items() if t.capability in pol.REACH_CAPABILITIES}
    assert reach_flagged == reach_capable


def test_gates_agree_with_tools_on_capability_wherever_both_declare_a_tool():
    # I1's other half: a plain-string _GATES entry for a name that is also in
    # TOOLS must name the SAME capability TOOLS does, so a _GATES typo (the
    # right tool, the wrong string) cannot silently disarm capability or reach
    # enforcement while the declarative TOOLS table still says the true thing.
    # Skips callable gates - update_ticket's future kwargs-dependent gate is
    # a *set* computed from the call, not a single capability to compare.
    for name, gate in pol._GATES.items():
        spec = pol.tools.TOOLS.get(name)
        if spec is None or not isinstance(gate, str):
            continue
        assert gate == spec.capability, f"{name}: _GATES says {gate!r}, TOOLS says {spec.capability!r}"


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
    # profile. A profile that grants a reach capability makes the switch
    # decorative. Fix wave I5: this used to hardcode TICKET_REPLY, so a second
    # reach capability (ticket.merge, added in the same fix wave as C1) could
    # have been granted by a profile with the suite still green. Iterating
    # REACH_CAPABILITIES itself closes that.
    for name, caps in pol.PROFILES.items():
        for reach_cap in pol.REACH_CAPABILITIES:
            assert reach_cap not in caps, f"profile {name!r} grants reach capability {reach_cap!r}"


# --- carried requirement 3: PROFILES must only name real capabilities ---------


def test_every_profile_only_grants_capabilities_that_exist():
    # Profile entries are hand-written literal sets, and nothing previously
    # asserted they only name capabilities ALL_CAPABILITIES actually declares. A
    # typo would grant a capability no gate requires and no test would notice -
    # which undercuts the reason profiles exist: "nobody composes a capability
    # list correctly under time pressure."
    all_caps = set(pol.ALL_CAPABILITIES)
    for name, caps in pol.PROFILES.items():
        assert caps <= all_caps, f"profile {name!r} grants unknown capabilities: {caps - all_caps}"


# --- carried requirement 2: REACH_CAPABILITIES must be CONSUMED by _dispatch --


def test_reach_is_derived_from_the_calls_required_capabilities_not_hand_listed(monkeypatch):
    # Proof this is derived rather than hand-listed: the fake method's NAME
    # ("fake_reply") appears nowhere in policy.py and is not in tools.TOOLS. The
    # only reason this call is stopped is that its gate's required capability
    # (TICKET_REPLY) intersects REACH_CAPABILITIES - exactly the mechanism the
    # carried requirement demands instead of a second, driftable list of names.
    monkeypatch.delenv("CSA_ZD_ALLOW_REACH", raising=False)
    monkeypatch.setitem(pol._GATES, "fake_reply", pol.TICKET_REPLY)
    monkeypatch.setattr(pol.PolicyBackend, "fake_reply", pol._make_gated("fake_reply"), raising=False)

    class BackendWithFakeReply(FakeBackend):
        def fake_reply(self, **kwargs: object) -> dict:
            return {"ok": True}  # pragma: no cover - refused before delegation

    pb = pol.PolicyBackend(BackendWithFakeReply(), pol.Policy(frozenset({pol.TICKET_REPLY})))
    with pytest.raises(exc.PolicyError, match="CSA_ZD_ALLOW_REACH"):
        pb.fake_reply(ticket_id=1)


def test_reach_derivation_lets_the_call_through_once_the_switch_is_on(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOW_REACH", "true")
    monkeypatch.setitem(pol._GATES, "fake_reply_2", pol.TICKET_REPLY)
    monkeypatch.setattr(pol.PolicyBackend, "fake_reply_2", pol._make_gated("fake_reply_2"), raising=False)

    class BackendWithFakeReply(FakeBackend):
        def fake_reply_2(self, **kwargs: object) -> dict:
            return {"ok": True, **kwargs}

    pb = pol.PolicyBackend(BackendWithFakeReply(), pol.Policy(frozenset({pol.TICKET_REPLY})))
    assert pb.fake_reply_2(ticket_id=1) == {"ok": True, "ticket_id": 1}


# --- scope wired end-to-end through _dispatch, not just unit-tested directly --


def test_get_ticket_through_the_real_dispatch_is_refused_outside_the_read_allowlist(monkeypatch):
    # test_tools.py proves policy.assert_subject_permitted() refuses in
    # isolation. This proves _dispatch actually calls it for a real,
    # materialised, gated method - not merely that the standalone function
    # works when called directly.
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_READ", "44821")
    pb = wrapped(tickets={99999: {"id": 99999}})
    with pytest.raises(exc.PolicyError, match="99999"):
        pb.get_ticket(ticket_id=99999)


def test_get_ticket_through_the_real_dispatch_permits_an_allowlisted_subject(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_READ", "44821")
    pb = wrapped(tickets={44821: {"id": 44821}})
    assert pb.get_ticket(ticket_id=44821) == {"ticket": {"id": 44821}}


def test_list_comments_through_the_real_dispatch_is_refused_outside_the_read_allowlist(monkeypatch):
    # Important 4 (final whole-branch review): `list_comments` previously had
    # no `tools.TOOLS` entry at all, so this call sailed through regardless of
    # `CSA_ZD_ALLOWLIST_READ` - the sibling of
    # `test_get_ticket_through_the_real_dispatch_is_refused_outside_the_read_
    # allowlist` above, now that `list_comments` carries the same
    # `subject_var`.
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_READ", "44821")
    pb = wrapped(tickets={99999: {"id": 99999}})
    with pytest.raises(exc.PolicyError, match="99999"):
        pb.list_comments(ticket_id=99999)


def test_list_comments_through_the_real_dispatch_permits_an_allowlisted_subject(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_READ", "44821")
    pb = wrapped(tickets={44821: {"id": 44821}})
    assert pb.list_comments(ticket_id=44821) == {"comments": []}


def test_dispatch_fails_closed_when_the_read_allowlist_is_entirely_unset(monkeypatch):
    # Fix round 1, finding 1: tests/conftest.py's autouse fixture defaults both
    # allowlists to "*" for every OTHER test in this suite, so the fail-closed-
    # on-unset behaviour was exercised at module level only (test_scope.py,
    # against a synthetic variable name) and never through a REAL dispatched
    # call. This is the one test that opts out of the default, to prove the
    # thing that actually matters: an allowlist nobody configured at all - not
    # a narrow one, not "*" - reaches _dispatch through a genuine
    # PolicyBackend.get_ticket call and refuses, naming the variable an
    # operator would set. Without this, a future "fix" that made
    # assert_subject_permitted silently pass on an unset variable would keep
    # the suite green while inverting the default from nothing-permitted to
    # everything-permitted.
    monkeypatch.delenv("CSA_ZD_ALLOWLIST_READ", raising=False)
    monkeypatch.delenv("CSA_ZD_ALLOWLIST_WRITE", raising=False)
    pb = wrapped()
    with pytest.raises(exc.PolicyError, match="CSA_ZD_ALLOWLIST_READ"):
        pb.get_ticket(ticket_id=1)


# --- fix round 1, finding 2: the tool's own constraint enforced AT THE SEAM ----


def test_a_tools_check_is_enforced_by_dispatch_itself_not_only_unit_tested(monkeypatch):
    # ADR-016 / this block's central claim: "the constraint is enforced at the
    # seam, not in the tool... PolicyBackend refuses the call." test_tools.py
    # proves ToolSpec.check functions reject the right kwargs when called
    # directly - that is a unit test of a function, not proof that _dispatch
    # is the thing doing the refusing. This installs a fake tool with a real
    # constraint, a fake gate, and a fake materialised method (the same
    # technique the capability- and reach-wiring tests above use) and proves
    # _dispatch calls spec.check(kwargs) itself: the malformed call is
    # refused, and the well-formed one is not.
    def reject_priority(kw: dict) -> None:
        if "priority" in kw:
            raise exc.PolicyError("this fake tool does not accept priority")

    monkeypatch.setitem(
        pol.tools.TOOLS, "fake_constrained", pol.tools.ToolSpec(capability=pol.TICKET_WRITE, check=reject_priority)
    )
    monkeypatch.setitem(pol._GATES, "fake_constrained", pol.TICKET_WRITE)
    monkeypatch.setattr(pol.PolicyBackend, "fake_constrained", pol._make_gated("fake_constrained"), raising=False)

    class BackendWithFakeConstrained(FakeBackend):
        def fake_constrained(self, **kwargs: object) -> dict:
            return {"ok": True, **kwargs}

    pb = pol.PolicyBackend(BackendWithFakeConstrained(), pol.Policy(frozenset({pol.TICKET_WRITE})))
    assert pb.fake_constrained(subject="x") == {"ok": True, "subject": "x"}
    with pytest.raises(exc.PolicyError, match="priority"):
        pb.fake_constrained(priority="high")


# --- update_ticket / assign_ticket: the first two writes, gated on ticket.write


def test_a_policy_without_ticket_write_refuses_update_ticket_before_reaching_the_backend():
    class ExplodingUpdate(FakeBackend):
        def update_ticket(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the backend must not be reached")

    pb = pol.PolicyBackend(ExplodingUpdate(), pol.Policy(frozenset()))
    with pytest.raises(exc.PolicyError, match="ticket.write"):
        pb.update_ticket(ticket_id=1, fields={"priority": "high"})


def test_a_policy_without_ticket_write_refuses_assign_ticket_before_reaching_the_backend():
    class ExplodingAssign(FakeBackend):
        def assign_ticket(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the backend must not be reached")

    pb = pol.PolicyBackend(ExplodingAssign(), pol.Policy(frozenset()))
    with pytest.raises(exc.PolicyError, match="ticket.write"):
        pb.assign_ticket(ticket_id=1, assignee_id=7)


def test_the_default_profile_can_update_a_ticket():
    pb = wrapped(tickets={1: {"id": 1, "priority": "low"}})
    assert pb.update_ticket(ticket_id=1, fields={"priority": "high"}) == {"ticket": {"id": 1, "priority": "high"}}


def test_the_default_profile_can_assign_a_ticket():
    pb = wrapped(tickets={1: {"id": 1}})
    assert pb.assign_ticket(ticket_id=1, assignee_id=7) == {"ticket": {"id": 1, "assignee_id": 7}}


def test_update_ticket_through_the_real_dispatch_is_refused_outside_the_write_allowlist(monkeypatch):
    # Sibling of test_get_ticket_through_the_real_dispatch_is_refused_outside_
    # the_read_allowlist, on the write allowlist this time - update_ticket is
    # the first tool this repo has ever scoped by CSA_ZD_ALLOWLIST_WRITE.
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    pb = wrapped(tickets={99999: {"id": 99999}})
    with pytest.raises(exc.PolicyError, match="99999"):
        pb.update_ticket(ticket_id=99999, fields={"priority": "high"})


def test_update_ticket_through_the_real_dispatch_permits_an_allowlisted_subject(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    pb = wrapped(tickets={44821: {"id": 44821}})
    assert pb.update_ticket(ticket_id=44821, fields={"priority": "high"}) == {
        "ticket": {"id": 44821, "priority": "high"}
    }


# --- CRITICAL regression: update_ticket must refuse a comment/status/reach-
# --- side-door NESTED INSIDE `fields`, not only one passed as an extra
# --- top-level kwarg. `policy._dispatch` calls `spec.run_check(kwargs)` with
# --- `kwargs == {"ticket_id": ..., "fields": {...}}` - a check that only
# --- looked at `kwargs` itself would see "comment" nested inside `fields` as
# --- nothing at all, and this exact call would have reached Zendesk as a
# --- public reply through a tool gated only on ticket.write, carrying no
# --- reach=True.


def test_update_ticket_through_the_real_dispatch_refuses_a_comment_nested_in_fields(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")

    class ExplodingIfCommented(FakeBackend):
        def update_ticket(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the backend must not be reached - this would have emailed the requester")

    pb = pol.PolicyBackend(ExplodingIfCommented(tickets={44821: {"id": 44821}}), pol.Policy.from_profile("default"))
    with pytest.raises(exc.PolicyError, match="comment"):
        pb.update_ticket(ticket_id=44821, fields={"comment": {"body": "surprise!", "public": True}})


def test_update_ticket_through_the_real_dispatch_refuses_a_status_nested_in_fields(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")

    class ExplodingIfSolved(FakeBackend):
        def update_ticket(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the backend must not be reached")

    pb = pol.PolicyBackend(ExplodingIfSolved(tickets={44821: {"id": 44821}}), pol.Policy.from_profile("default"))
    with pytest.raises(exc.PolicyError, match="status"):
        pb.update_ticket(ticket_id=44821, fields={"status": "solved"})


@pytest.mark.parametrize(
    "key,value",
    [
        ("custom_status_id", 321),
        ("additional_collaborators", ["a@example.com"]),
        ("email_ccs", [{"user_email": "a@example.com", "action": "put"}]),
        ("followers", [{"user_email": "a@example.com", "action": "put"}]),
        ("collaborator_ids", [123]),
    ],
)
def test_update_ticket_through_the_real_dispatch_refuses_each_reach_side_door_nested_in_fields(monkeypatch, key, value):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")

    class ExplodingIfSideDoored(FakeBackend):
        def update_ticket(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the backend must not be reached")

    pb = pol.PolicyBackend(ExplodingIfSideDoored(tickets={44821: {"id": 44821}}), pol.Policy.from_profile("default"))
    with pytest.raises(exc.PolicyError, match=key):
        pb.update_ticket(ticket_id=44821, fields={key: value})


def test_assign_ticket_through_the_real_dispatch_is_refused_outside_the_write_allowlist(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    pb = wrapped(tickets={99999: {"id": 99999}})
    with pytest.raises(exc.PolicyError, match="99999"):
        pb.assign_ticket(ticket_id=99999, assignee_id=7)


def test_assign_ticket_through_the_real_dispatch_permits_an_allowlisted_subject(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    pb = wrapped(tickets={44821: {"id": 44821}})
    assert pb.assign_ticket(ticket_id=44821, group_id=9) == {"ticket": {"id": 44821, "group_id": 9}}


# --- add_internal_note / solve_ticket: THE control this block exists to get
# right (public forced by construction, never by input) - see backend.py


def test_a_policy_without_ticket_note_refuses_add_internal_note_before_reaching_the_backend():
    class ExplodingNote(FakeBackend):
        def add_internal_note(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the backend must not be reached")

    pb = pol.PolicyBackend(ExplodingNote(), pol.Policy(frozenset()))
    with pytest.raises(exc.PolicyError, match="ticket.note"):
        pb.add_internal_note(ticket_id=1, body="hi")


def test_a_policy_without_ticket_solve_refuses_solve_ticket_before_reaching_the_backend():
    class ExplodingSolve(FakeBackend):
        def solve_ticket(self, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("the backend must not be reached")

    pb = pol.PolicyBackend(ExplodingSolve(), pol.Policy(frozenset()))
    with pytest.raises(exc.PolicyError, match="ticket.solve"):
        pb.solve_ticket(ticket_id=1)


def test_the_default_profile_can_add_an_internal_note():
    pb = wrapped(tickets={1: {"id": 1}})
    assert pb.add_internal_note(ticket_id=1, body="internal") == {"ticket": {"id": 1}}


def test_the_default_profile_cannot_solve_a_ticket():
    # TICKET_SOLVE is not in the reversible-only default profile (it is an
    # on-ramp to a terminal state) - "agent" is the profile that carries it.
    pb = wrapped(tickets={1: {"id": 1}})
    with pytest.raises(exc.PolicyError, match=pol.TICKET_SOLVE):
        pb.solve_ticket(ticket_id=1)


def test_the_agent_profile_can_solve_a_ticket():
    pb = wrapped(profile="agent", tickets={1: {"id": 1, "status": "open"}})
    assert pb.solve_ticket(ticket_id=1) == {"ticket": {"id": 1, "status": "solved"}}


def test_add_internal_note_through_the_real_dispatch_is_refused_outside_the_write_allowlist(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    pb = wrapped(tickets={99999: {"id": 99999}})
    with pytest.raises(exc.PolicyError, match="99999"):
        pb.add_internal_note(ticket_id=99999, body="hi")


def test_add_internal_note_through_the_real_dispatch_permits_an_allowlisted_subject(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    pb = wrapped(tickets={44821: {"id": 44821}})
    assert pb.add_internal_note(ticket_id=44821, body="hi") == {"ticket": {"id": 44821}}


def test_add_internal_note_through_the_real_dispatch_cannot_be_made_public(monkeypatch):
    # The end-to-end proof of this block's central claim: even through the
    # real seam, with an allowlisted subject and the capability granted, a
    # caller attempting to pass public=True gets a clean PolicyError, not a
    # public note.
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    pb = wrapped(tickets={44821: {"id": 44821}})
    with pytest.raises(exc.PolicyError, match="public"):
        pb.add_internal_note(ticket_id=44821, body="hi", public=True)


def test_solve_ticket_through_the_real_dispatch_is_refused_outside_the_write_allowlist(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    pb = wrapped(profile="agent", tickets={99999: {"id": 99999}})
    with pytest.raises(exc.PolicyError, match="99999"):
        pb.solve_ticket(ticket_id=99999)


def test_solve_ticket_through_the_real_dispatch_permits_an_allowlisted_subject(monkeypatch):
    monkeypatch.setenv("CSA_ZD_ALLOWLIST_WRITE", "44821")
    pb = wrapped(profile="agent", tickets={44821: {"id": 44821, "status": "open"}})
    assert pb.solve_ticket(ticket_id=44821) == {"ticket": {"id": 44821, "status": "solved"}}
