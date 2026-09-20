import inspect

from csa_zendesk import exceptions as exc


def test_every_error_descends_from_the_base():
    for name in exc.__all__:
        cls = getattr(exc, name)
        assert issubclass(cls, exc.ZendeskError), name


def test_validation_error_carries_problems_keyed_by_field():
    # details is a MAP from field name to problems - `base` for whole-record
    # issues, the field name for field-scoped ones. Never index one key.
    e = exc.ValidationError(
        "refused",
        problems={
            "base": [{"description": "Assignee: is required when solving a ticket"}],
            "status": [{"description": "closed prevents ticket update"}],
        },
    )
    assert set(e.problems) == {"base", "status"}
    assert "Assignee" in str(e)


def test_plan_boundary_is_not_confused_with_an_outage():
    assert not issubclass(exc.PlanBoundary, exc.ServiceUnavailable)
    assert not issubclass(exc.ServiceUnavailable, exc.PlanBoundary)


def test_errors_declare_only_approved_parameters():
    # Guard against the whole class of credential leak, fail-closed.
    #
    # This is an ALLOWLIST, not a denylist. A denylist only catches the names someone
    # thought of - `api_key`, `bearer`, `auth` and `cookie` all sail past a list built
    # around `token` and `password`. This repo has paid for that shape once already
    # (CLAUDE.md, "the denylist is the disclosure"), so an unrecognised parameter name
    # fails here and the remedy is a deliberate edit to this set. That edit is the
    # review checkpoint: it is where you notice you are about to put a credential on an
    # object embedders log.
    #
    # A class that declares no __init__ of its own inherits Exception's, which takes only
    # *args - no named parameter, nothing to leak - and inspect.signature() cannot read a
    # C slot wrapper anyway. Those are skipped; the guard stays live exactly where it can
    # bite, because carrying a field REQUIRES declaring an __init__.
    # `remedy` added deliberately (fix wave I7, final whole-branch review):
    # `CredentialsRejected.remedy` is this library's OWN guidance text, never
    # interpolated from a Zendesk response body or any other external input -
    # see that class's docstring. Reviewed and approved here, which is the
    # point of this guard.
    approved = {"self", "message", "problems", "retry_after", "status", "remedy"}
    checked = 0
    for name in exc.__all__:
        cls = getattr(exc, name)
        own = next((vars(k)["__init__"] for k in cls.__mro__ if "__init__" in vars(k)), None)
        if own is None or own in (Exception.__init__, BaseException.__init__, object.__init__):
            continue
        checked += 1
        for p in inspect.signature(own).parameters:
            assert p in approved, f"{name}.{p} is not an approved error parameter"
    # If this ever reaches zero the guard has gone vacuous while still passing.
    assert checked == 5, f"expected 5 inspectable errors, found {checked}"


def test_rate_limited_carries_message_and_retry_after():
    e = exc.RateLimited("slow down", retry_after=30)
    assert e.retry_after == 30
    assert "slow down" in str(e)


def test_service_unavailable_carries_retry_after():
    e = exc.ServiceUnavailable("maintenance", retry_after=10)
    assert e.retry_after == 10


def test_api_error_carries_status():
    e = exc.ApiError("upstream broke", status=500)
    assert e.status == 500


def test_api_error_status_defaults_to_zero():
    e = exc.ApiError("no status given")
    assert e.status == 0
