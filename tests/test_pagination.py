import pytest

from csa_zendesk import exceptions as exc
from csa_zendesk._pagination import (
    CURSOR_SORTABLE,
    check_params,
    is_cursor_response,
    next_cursor,
    translate_sort,
)


def test_cursor_params_alone_are_fine():
    check_params({"page[size]": 100, "sort": "-updated_at"})


def test_offset_params_alone_are_fine():
    check_params({"per_page": 100, "sort_by": "created_at", "sort_order": "desc"})


def test_mixing_the_two_styles_is_refused():
    # Live behaviour: Zendesk returns 200 and SILENTLY DISCARDS the sort, giving
    # default ordering. Byte-identical to sending no sort at all. We refuse.
    with pytest.raises(exc.PaginationError, match="both pagination styles"):
        check_params({"page[size]": 3, "sort_by": "updated_at", "sort_order": "desc"})
    with pytest.raises(exc.PaginationError):
        check_params({"page[after]": "abc", "per_page": 10})


def test_a_sort_field_cursor_paging_cannot_honour_is_refused_locally():
    # Live: sort=created_at with page[size] -> 400 InvalidPaginationParameter.
    # Refusing locally saves a round trip and gives a better message.
    with pytest.raises(exc.PaginationError, match="created_at"):
        check_params({"page[size]": 10, "sort": "created_at"})
    with pytest.raises(exc.PaginationError):
        check_params({"page[size]": 10, "sort": "-assignee.name"})


def test_the_three_cursor_sortable_fields_are_accepted_in_both_directions():
    assert CURSOR_SORTABLE == frozenset({"updated_at", "id", "status"})
    for field in CURSOR_SORTABLE:
        check_params({"page[size]": 10, "sort": field})
        check_params({"page[size]": 10, "sort": f"-{field}"})


def test_style_is_detected_from_the_response_not_the_request():
    # Some endpoints answer cursor-shaped whether or not you asked.
    assert is_cursor_response({"tickets": [], "meta": {"has_more": False}, "links": {}})
    assert not is_cursor_response({"tickets": [], "next_page": None, "count": 0})
    assert not is_cursor_response({"meta": {"has_more": True}})  # meta without links


def test_next_cursor_is_none_when_there_is_no_more():
    assert next_cursor({"meta": {"has_more": False, "after_cursor": "x"}, "links": {}}) is None
    assert next_cursor({"meta": {"has_more": True, "after_cursor": "abc"}, "links": {}}) == "abc"
    assert next_cursor({"count": 3, "next_page": None}) is None


def test_newest_first_translates_to_id_because_created_at_is_not_cursor_sortable():
    assert translate_sort("-created_at") == "-id"
    assert translate_sort("created_at") == "id"
    assert translate_sort("-updated_at") == "-updated_at"


def test_check_params_refusal_names_the_conflicting_keys_and_the_remedy():
    # Every refusal in this project names its own remedy - assert the message
    # actually identifies which parameters conflicted, not just that something
    # was wrong.
    with pytest.raises(exc.PaginationError) as excinfo:
        check_params({"page[size]": 3, "sort_by": "updated_at", "sort_order": "desc"})
    message = str(excinfo.value)
    assert "page[size]" in message
    assert "sort_by" in message
    assert "sort_order" in message


def test_none_valued_params_are_not_treated_as_present():
    # A caller building a params dict from optional arguments may pass through
    # keys with a None value rather than omitting them; that must not look
    # like a conflicting parameter was sent.
    check_params({"page[size]": 10, "sort": "-id", "per_page": None, "sort_by": None})
