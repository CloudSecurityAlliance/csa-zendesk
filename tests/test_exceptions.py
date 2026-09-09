from csa_zendesk import exceptions as exc


def test_every_error_descends_from_the_base() -> None:
    for name in exc.__all__:
        cls = getattr(exc, name)
        assert issubclass(cls, exc.ZendeskError), name


def test_validation_error_carries_problems_keyed_by_field() -> None:
    # details is a MAP from field name to problems - `base` for whole-record
    # issues, the field name for field-scoped ones. Never index one key.
    e = exc.ValidationError("refused", problems={
        "base": [{"description": "Assignee: is required when solving a ticket"}],
        "status": [{"description": "closed prevents ticket update"}],
    })
    assert set(e.problems) == {"base", "status"}
    assert "Assignee" in str(e)


def test_rate_limited_carries_retry_after() -> None:
    assert exc.RateLimited("slow down", retry_after=42).retry_after == 42


def test_plan_boundary_is_not_confused_with_an_outage() -> None:
    assert not issubclass(exc.PlanBoundary, exc.ServiceUnavailable)
    assert not issubclass(exc.ServiceUnavailable, exc.PlanBoundary)


def test_credentials_are_never_interpolated_into_a_message() -> None:
    # Guard against the whole class of leak: no error takes a credential.
    import inspect
    for name in exc.__all__:
        sig = inspect.signature(getattr(exc, name))
        for p in sig.parameters:
            assert p not in ("token", "password", "secret", "api_token"), f"{name}.{p}"
