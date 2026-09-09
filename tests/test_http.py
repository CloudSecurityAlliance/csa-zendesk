import base64

import httpx
import pytest

from csa_zendesk import _http
from csa_zendesk import exceptions as exc
from csa_zendesk._http import HttpClient


def client(handler, **kw):
    return HttpClient(
        subdomain="example", email="agent@example.com", api_token="tok", transport=httpx.MockTransport(handler), **kw
    )


def test_a_get_returns_the_parsed_envelope_unshaped():
    def handler(request):
        assert request.url.path == "/api/v2/tickets/1.json"
        return httpx.Response(200, json={"ticket": {"id": 1, "subject": "hi"}})

    assert client(handler).get("/api/v2/tickets/1.json") == {"ticket": {"id": 1, "subject": "hi"}}


def test_it_sends_api_token_basic_auth_in_the_email_slash_token_form():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})

    client(handler).get("/api/v2/tickets.json")
    expected = base64.b64encode(b"agent@example.com/token:tok").decode()
    assert seen["auth"] == f"Basic {expected}"


def test_no_credential_ever_appears_in_an_exception_message():
    def handler(request):
        return httpx.Response(401, json={"error": "Couldn't authenticate you"})

    with pytest.raises(exc.CredentialsRejected) as ei:
        client(handler).get("/api/v2/tickets.json")
    assert "tok" not in str(ei.value)


def test_no_credential_ever_appears_in_repr():
    c = client(lambda request: httpx.Response(200, json={}))
    assert "tok" not in repr(c)
    assert "agent@example.com" not in repr(c)


def test_a_200_that_is_not_json_is_an_error_not_a_return_value():
    def handler(request):
        return httpx.Response(200, text="<html>hello</html>")

    with pytest.raises(exc.ApiError, match="not JSON"):
        client(handler).get("/api/v2/tickets.json")


def test_a_200_whose_json_is_not_an_object_is_an_error():
    def handler(request):
        return httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(exc.ApiError, match="not a JSON object"):
        client(handler).get("/api/v2/tickets.json")


def test_an_error_response_with_a_non_json_body_is_still_a_typed_error():
    # Exercises the ValueError branch of the error-path body parser: a 5xx that
    # answers with plain text (a proxy error page, say) rather than Zendesk's own
    # JSON envelope must not blow up trying to parse it - it becomes ApiError via
    # parse_error's own "not a JSON object" handling of a None body.
    def handler(request):
        return httpx.Response(500, text="upstream proxy error, not JSON at all")

    with pytest.raises(exc.ApiError, match="not a JSON object"):
        client(handler).get("/api/v2/tickets.json")


def test_429_is_retried_honouring_retry_after_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    assert client(handler).get("/api/v2/tickets.json") == {"ok": True}
    assert calls["n"] == 2


def test_503_is_retried_for_an_idempotent_request():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={}, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    assert client(handler).request("GET", "/api/v2/tickets.json") == {"ok": True}


def test_503_is_NOT_retried_for_a_non_idempotent_write():
    # The mutation may already have landed. Retrying could double-apply it.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={}, headers={"Retry-After": "0"})

    with pytest.raises(exc.ServiceUnavailable):
        client(handler).request("POST", "/api/v2/tickets.json", json={}, idempotent=False)
    assert calls["n"] == 1


def test_429_IS_retried_even_for_a_non_idempotent_write():
    # A rate limit means the request was refused, not applied.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "0"})
        return httpx.Response(201, json={"ok": True})

    assert client(handler).request("POST", "/api/v2/tickets.json", json={}, idempotent=False) == {"ok": True}
    assert calls["n"] == 2


def test_retries_are_bounded():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={}, headers={"Retry-After": "0"})

    with pytest.raises(exc.RateLimited):
        client(handler).get("/api/v2/tickets.json")
    assert calls["n"] <= 1 + 3


def test_mixed_pagination_params_are_refused_before_any_request_is_made():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={})

    with pytest.raises(exc.PaginationError):
        client(handler).get("/api/v2/tickets.json", params={"page[size]": 5, "sort_by": "updated_at"})
    assert calls["n"] == 0


def test_none_valued_params_are_dropped_rather_than_sent_as_none():
    def handler(request):
        assert "sort" not in request.url.params
        assert request.url.params.get("page[size]") == "5"
        return httpx.Response(200, json={})

    client(handler).get("/api/v2/tickets.json", params={"page[size]": 5, "sort": None})


def test_a_transport_level_failure_becomes_a_typed_error():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(exc.ApiError, match="could not reach Zendesk"):
        client(handler).get("/api/v2/tickets.json")


def test_subdomain_is_required():
    with pytest.raises(ValueError, match="ZENDESK_SUBDOMAIN"):
        HttpClient(subdomain="", email="agent@example.com", api_token="tok")


def test_email_is_required():
    with pytest.raises(ValueError, match="email"):
        HttpClient(subdomain="example", email="", api_token="tok")


def test_api_token_is_required():
    with pytest.raises(ValueError, match="token"):
        HttpClient(subdomain="example", email="agent@example.com", api_token="")


def test_the_client_sleeps_for_exactly_the_reported_retry_after_value(monkeypatch):
    # The backoff schedule itself, made assertable: no real sleep ever happens in
    # this suite. A monkeypatched time.sleep records what it would have waited for
    # instead of actually waiting.
    slept = []
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: slept.append(seconds))
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    assert client(handler).get("/api/v2/tickets.json") == {"ok": True}
    assert slept == [7]


def test_a_retry_after_beyond_the_cap_is_never_slept_and_stops_retrying_immediately(monkeypatch):
    # Probe-verified: Zendesk can report an absurd Retry-After (999999 observed
    # live). parse_error reports that value faithfully - the ceiling is ours to
    # enforce, by not waiting it out and not silently shortening it either.
    slept = []
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: slept.append(seconds))
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={}, headers={"Retry-After": "999999"})

    with pytest.raises(exc.RateLimited) as ei:
        client(handler).get("/api/v2/tickets.json")
    assert ei.value.retry_after == 999999  # reported exactly as the server said, never shortened
    assert slept == []  # and never actually waited on
    assert calls["n"] == 1  # gave up immediately rather than looping


def test_a_503_retry_after_beyond_the_cap_is_also_refused(monkeypatch):
    slept = []
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: slept.append(seconds))

    def handler(request):
        return httpx.Response(503, json={}, headers={"Retry-After": "999999"})

    with pytest.raises(exc.ServiceUnavailable) as ei:
        client(handler).request("GET", "/api/v2/tickets.json")
    assert ei.value.retry_after == 999999
    assert slept == []
