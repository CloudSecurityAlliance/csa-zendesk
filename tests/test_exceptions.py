import inspect

from csa_zendesk import exceptions as exc


def test_every_error_descends_from_the_base() -> None:
    for name in exc.__all__:
        cls = getattr(exc, name)
        assert issubclass(cls, exc.ZendeskError), name


def test_validation_error_carries_problems_keyed_by_field() -> None:
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


def test_rate_limited_carries_retry_after() -> None:
    assert exc.RateLimited("slow down", retry_after=42).retry_after == 42


def test_plan_boundary_is_not_confused_with_an_outage() -> None:
    assert not issubclass(exc.PlanBoundary, exc.ServiceUnavailable)
    assert not issubclass(exc.ServiceUnavailable, exc.PlanBoundary)


def test_credentials_are_never_interpolated_into_a_message() -> None:
    # Guard against the whole class of leak: no error takes a credential.
    #
    # A class that declares no __init__ of its own inherits Exception's, which takes
    # only *args - there is no named parameter that could be a credential, and
    # inspect.signature() cannot read a C slot wrapper anyway (it raises ValueError on
    # a bare Exception subclass). So those are skipped, and the guard stays live exactly
    # where it can bite: the moment an exception is given a field of its own, it must
    # declare an __init__, and that __init__ is checked.
    forbidden = {
        "token",
        "password",
        "secret",
        "api_token",
        "access_token",
        "refresh_token",
        "client_secret",
        "credential",
        "authorization",
    }
    checked = 0
    for name in exc.__all__:
        cls = getattr(exc, name)
        own = next((vars(k)["__init__"] for k in cls.__mro__ if "__init__" in vars(k)), None)
        if own is None or own is BaseException.__init__ or own is Exception.__init__ or own is object.__init__:
            continue
        checked += 1
        for p in inspect.signature(own).parameters:
            assert p not in forbidden, f"{name}.{p}"
    # If this ever drops to zero the guard has gone vacuous - four classes carry payload.
    assert checked == 4, f"expected 4 inspectable errors, found {checked}"


def test_rate_limited_carries_message_and_retry_after() -> None:
    e = exc.RateLimited("slow down", retry_after=30)
    assert e.retry_after == 30
    assert "slow down" in str(e)


def test_service_unavailable_carries_retry_after() -> None:
    e = exc.ServiceUnavailable("maintenance", retry_after=10)
    assert e.retry_after == 10


def test_api_error_carries_status() -> None:
    e = exc.ApiError("upstream broke", status=500)
    assert e.status == 500


def test_api_error_status_defaults_to_zero() -> None:
    e = exc.ApiError("no status given")
    assert e.status == 0
