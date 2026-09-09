from csa_zendesk import exceptions as exc
from csa_zendesk._errors import extract_problems, parse_error


def test_shape_1_error_as_an_object():
    e = parse_error(403, {"error": {"title": "Forbidden",
                                    "message": "You do not have access to this page."}})
    assert isinstance(e, exc.PlanBoundary)


def test_shape_2_error_as_a_string_with_a_description():
    assert isinstance(parse_error(404, {"error": "RecordNotFound",
                                        "description": "Not found"}), exc.NotFound)
    assert isinstance(parse_error(404, {"error": "InvalidEndpoint",
                                        "description": "Not found"}), exc.EndpointNotAvailable)


def test_shape_3_errors_as_an_array():
    e = parse_error(400, {"errors": [{"code": "InvalidPaginationDepth",
                                      "title": "Pagination requests using Offset Pagination are limited"}]})
    assert isinstance(e, exc.PaginationError)


def test_shape_4_string_plus_description_plus_details():
    e = parse_error(422, {
        "error": "RecordInvalid", "description": "Record validation errors",
        "details": {"base": [
            {"description": "Assignee: is required when solving a ticket",
             "ticket_field_id": 1, "ticket_field_type": "FieldAssignee"}]}})
    assert isinstance(e, exc.ValidationError)
    assert "Assignee" in str(e)


def test_details_keyed_by_field_not_only_base():
    # The closed-ticket refusal keys details by the FIELD name. A parser that
    # reads details.base finds nothing here.
    e = parse_error(422, {"error": "RecordInvalid", "description": "Record validation errors",
                          "details": {"status": [{"description": "closed prevents ticket update"}]}})
    assert isinstance(e, exc.ValidationError)
    assert set(e.problems) == {"status"}
    assert "closed prevents ticket update" in str(e)


def test_a_parser_reading_only_error_and_description_would_lose_the_diagnosis():
    body = {"error": "RecordInvalid", "description": "Record validation errors",
            "details": {"base": [{"description": "the actual reason"}]}}
    assert "the actual reason" in str(parse_error(422, body))


def test_bare_string_error():
    assert isinstance(parse_error(401, {"error": "Couldn't authenticate you"}),
                      exc.CredentialsRejected)


def test_search_response_limit():
    e = parse_error(422, {"error": "invalid",
                          "description": "Invalid search: Requested response size was greater "
                                         "than Search Response Limits"})
    assert isinstance(e, exc.SearchLimitExceeded)


def test_rate_limited_reads_retry_after():
    e = parse_error(429, {}, headers={"Retry-After": "17"})
    assert isinstance(e, exc.RateLimited) and e.retry_after == 17


def test_rate_limited_defaults_when_the_header_is_absent():
    # 10s is the official Ruby client's DEFAULT_RETRY_AFTER.
    e = parse_error(429, {}, headers={})
    assert isinstance(e, exc.RateLimited) and e.retry_after == 10


def test_503_is_service_unavailable_and_carries_retry_after():
    e = parse_error(503, {}, headers={"Retry-After": "30"})
    assert isinstance(e, exc.ServiceUnavailable) and e.retry_after == 30


def test_a_body_that_is_not_a_dict_is_still_an_error_not_a_crash():
    assert isinstance(parse_error(500, "<html>gateway</html>"), exc.ApiError)
    assert isinstance(parse_error(500, None), exc.ApiError)


def test_unknown_status_becomes_apierror_carrying_the_status():
    e = parse_error(418, {"error": "teapot"})
    assert isinstance(e, exc.ApiError) and e.status == 418


def test_extract_problems_ignores_a_details_that_is_not_a_map():
    assert extract_problems({"details": "nope"}) == {}
    assert extract_problems({"details": {"base": "not a list"}}) == {}
