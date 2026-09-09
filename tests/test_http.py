import base64
import binascii
import copy

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


# --- fix round 1 -------------------------------------------------------------
#
# Two Criticals (both credential leaks), one misclassified success, two boundary
# gaps. See .superpowers/sdd/2026-09-08-block-0-foundations/task-5-fix-1.md.


def test_retry_after_at_exactly_the_cap_is_slept_and_succeeds(monkeypatch):
    # The boundary itself: MAX_RETRY_AFTER_SECONDS is meant as an inclusive
    # maximum, so exactly the cap must still be waited out.
    slept = []
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: slept.append(seconds))
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": str(_http.MAX_RETRY_AFTER_SECONDS)})
        return httpx.Response(200, json={"ok": True})

    assert client(handler).get("/api/v2/tickets.json") == {"ok": True}
    assert slept == [_http.MAX_RETRY_AFTER_SECONDS]
    assert calls["n"] == 2


def test_retry_after_one_second_past_the_cap_is_refused_not_shortened(monkeypatch):
    slept = []
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: slept.append(seconds))
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={}, headers={"Retry-After": str(_http.MAX_RETRY_AFTER_SECONDS + 1)})

    with pytest.raises(exc.RateLimited) as ei:
        client(handler).get("/api/v2/tickets.json")
    assert ei.value.retry_after == _http.MAX_RETRY_AFTER_SECONDS + 1  # reported, not shortened
    assert slept == []
    assert calls["n"] == 1


def test_a_200_whose_body_is_a_bare_json_string_is_an_error():
    def handler(request):
        return httpx.Response(200, json="just a string")

    with pytest.raises(exc.ApiError, match="not a JSON object"):
        client(handler).get("/api/v2/tickets.json")


def test_a_200_whose_body_is_a_bare_json_number_is_an_error():
    def handler(request):
        return httpx.Response(200, json=42)

    with pytest.raises(exc.ApiError, match="not a JSON object"):
        client(handler).get("/api/v2/tickets.json")


def test_a_200_whose_body_is_json_null_is_an_error():
    # httpx.Response(..., json=None) treats None as "no body provided" (mirroring
    # Python's own None-as-default convention) and produces an empty response, not
    # the four bytes b"null" - so the literal JSON `null` has to be built as
    # explicit content to actually exercise this shape.
    def handler(request):
        return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})

    with pytest.raises(exc.ApiError, match="not a JSON object"):
        client(handler).get("/api/v2/tickets.json")


def test_a_204_no_content_is_a_success_with_an_empty_envelope():
    # DeleteTicket and 121 other inventoried DELETE operations document 204.
    # This is a *different* answer from the wrong-shape tests above: no body at
    # all is a success, not a malformed response.
    def handler(request):
        return httpx.Response(204)

    assert client(handler).request("DELETE", "/api/v2/tickets/1.json") == {}


def test_a_200_with_a_zero_length_body_is_also_a_success_with_an_empty_envelope():
    # The same situation as 204, arriving under a different status code - judged
    # on the evidence (no content), not on status == 204 specifically.
    def handler(request):
        return httpx.Response(200, content=b"")

    assert client(handler).get("/api/v2/tickets.json") == {}


def test_the_credential_is_not_reachable_from_the_instance_dict():
    # repr() was already redacted - that is what made the leak invisible. base64
    # is not obfuscation, so decode anything string-shaped in __dict__ (and in a
    # shallow copy's __dict__) before asserting it is clean. This must fail
    # against the pre-fix code, where `vars(client)['_auth']` is the base64 of
    # exactly this string.
    token = "tok"
    email = "agent@example.com"
    c = client(lambda request: httpx.Response(200, json={}))

    def leaks_credential(value: object) -> bool:
        if not isinstance(value, str):
            return False
        if token in value or email in value:
            return True
        # A header value like "Basic <base64>" isn't itself valid base64 (the
        # scheme name and the space aren't in the alphabet), so try each
        # whitespace-separated piece rather than the whole string.
        for piece in value.split():
            padded = piece + "=" * (-len(piece) % 4)
            try:
                decoded = base64.b64decode(padded, validate=False).decode("utf-8", errors="ignore")
            except (binascii.Error, ValueError):
                continue
            if token in decoded or email in decoded:
                return True
        return False

    for obj in (c, copy.copy(c)):
        for name, value in vars(obj).items():
            assert not leaks_credential(value), f"credential reachable via vars(client)[{name!r}] = {value!r}"


def test_a_transport_error_does_not_chain_to_an_exception_carrying_the_auth_header():
    # __cause__ must be severed, AND Python's *implicit* __context__ chaining -
    # which a bare `from None` does not clear - must not silently carry the same
    # reference: either one is a path a crash reporter or error tracker can walk
    # straight to the live Authorization header on e.request.
    def handler(request):
        raise httpx.ConnectError("no route to host", request=request)

    with pytest.raises(exc.ApiError) as ei:
        client(handler).get("/api/v2/tickets.json")

    assert ei.value.__cause__ is None
    assert ei.value.__context__ is None
    assert "tok" not in str(ei.value)
