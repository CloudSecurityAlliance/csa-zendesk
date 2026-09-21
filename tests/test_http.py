import base64
import binascii
import copy

import httpx
import pytest

from csa_zendesk import _http
from csa_zendesk import exceptions as exc
from csa_zendesk._http import HttpClient

#: The stand-in access token. Deliberately not a short word: the leak tests assert
#: this string is absent from reprs, __dict__s and exception messages, and a canary
#: that appears inside ordinary prose produces false positives. "tok" did - it is a
#: substring of "token", which any credential-related error message will contain.
CANARY = "zdt-CANARY-8f2a1c-DO-NOT-LOG"


def client(handler, **kw):
    return HttpClient(subdomain="example", token_provider=lambda: CANARY, transport=httpx.MockTransport(handler), **kw)


def test_a_get_returns_the_parsed_envelope_unshaped():
    def handler(request):
        assert request.url.path == "/api/v2/tickets/1.json"
        return httpx.Response(200, json={"ticket": {"id": 1, "subject": "hi"}})

    assert client(handler).get("/api/v2/tickets/1.json") == {"ticket": {"id": 1, "subject": "hi"}}


def test_it_sends_the_oauth_access_token_as_a_bearer_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})

    client(handler).get("/api/v2/tickets.json")
    assert seen["auth"] == f"Bearer {CANARY}"


def test_the_provider_is_called_per_request_so_a_refreshed_token_is_picked_up():
    # ADR-009: Zendesk issues a 30-minute expires_in automatically, so a token
    # captured once at construction expires mid-session. The provider must be
    # consulted on every request, not cached - this is the test that fails if
    # someone "optimises" it into a constructor-time lookup.
    tokens = iter(["first", "second", "third"])
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={})

    c = _http.HttpClient(
        subdomain="example",
        token_provider=lambda: next(tokens),
        transport=httpx.MockTransport(handler),
    )
    for _ in range(3):
        c.get("/api/v2/tickets.json")
    assert seen == ["Bearer first", "Bearer second", "Bearer third"]


def test_no_credential_ever_appears_in_an_exception_message():
    def handler(request):
        return httpx.Response(401, json={"error": "Couldn't authenticate you"})

    with pytest.raises(exc.CredentialsRejected) as ei:
        client(handler).get("/api/v2/tickets.json")
    assert CANARY not in str(ei.value)


def test_no_credential_ever_appears_in_repr():
    c = client(lambda request: httpx.Response(200, json={}))
    assert CANARY not in repr(c)
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
    with pytest.raises(ValueError, match="CSA_ZENDESK_SUBDOMAIN"):
        HttpClient(subdomain="", token_provider=lambda: CANARY)


def test_a_non_callable_token_provider_is_refused_at_construction():
    with pytest.raises(ValueError, match="callable"):
        HttpClient(subdomain="example", token_provider="a-bare-string")  # type: ignore[arg-type]


def test_an_empty_token_fails_at_request_time_rather_than_silently_unauthenticated():
    # The provider is only consulted per request, so emptiness cannot be caught
    # at construction. It must still never produce a bare "Bearer " header - an
    # unauthenticated request to Zendesk answers 200 with an "Anonymous user"
    # object (CLAUDE.md invariant 1), which is the silent failure this guards.
    def handler(request):  # pragma: no cover - must never be reached
        raise AssertionError("a request was sent with an empty access token")

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: "",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(exc.CredentialsRejected, match="empty access token"):
        c.get("/api/v2/tickets.json")


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


# --- retry boundaries, credential-leak paths, and response-shape edge cases --
#
# Two credential-leak paths (the instance dict, and exception chaining carrying
# the live Authorization header), one success case that was previously
# misclassified as an error (a 200/204 with an empty body), and two off-by-one
# boundaries in the retry ceiling below.


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
    token = CANARY
    c = client(lambda request: httpx.Response(200, json={}))

    def leaks_credential(value: object) -> bool:
        if not isinstance(value, str):
            return False
        if token in value:
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
            if token in decoded:
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
    assert CANARY not in str(ei.value)


# --- cumulative retry budget --------------------------------------------------
#
# MAX_RETRIES x MAX_RETRY_AFTER_SECONDS alone still allows 180s of real sleeping in
# one logical call (see MAX_TOTAL_RETRY_SECONDS's docstring in _http.py for why
# that is an ordinary production sequence, not a pathological one).


def test_a_sustained_59s_retry_after_stops_on_the_budget_not_on_max_retries(monkeypatch):
    # A Retry-After of 59s stays under the per-sleep cap (60s) every single time, so
    # nothing here is ever refused for being an absurd single wait - only the sum
    # across attempts trips the budget.
    slept = []
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: slept.append(seconds))
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={}, headers={"Retry-After": "59"})

    with pytest.raises(exc.RateLimited) as ei:
        client(handler).get("/api/v2/tickets.json")

    assert sum(slept) <= _http.MAX_TOTAL_RETRY_SECONDS
    assert calls["n"] < 1 + _http.MAX_RETRIES  # gave up before exhausting MAX_RETRIES
    assert "budget" in str(ei.value)
    assert ei.value.retry_after == 59  # the underlying fact is preserved, not altered


def test_a_small_retry_after_still_gets_the_full_max_retries(monkeypatch):
    # The budget must not quietly become the binding limit for ordinary retries: a
    # server that keeps saying "retry in 1s" should still be retried MAX_RETRIES
    # times, exactly as it was before the budget existed.
    slept = []
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: slept.append(seconds))
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={}, headers={"Retry-After": "1"})

    with pytest.raises(exc.RateLimited) as ei:
        client(handler).get("/api/v2/tickets.json")

    assert calls["n"] == 1 + _http.MAX_RETRIES
    assert slept == [1] * _http.MAX_RETRIES
    assert "budget" not in str(ei.value)  # gave up on MAX_RETRIES, not the budget


def test_the_budget_exhausted_message_names_the_budget_as_the_reason(monkeypatch):
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)

    def handler(request):
        return httpx.Response(503, json={}, headers={"Retry-After": "59"})

    with pytest.raises(exc.ServiceUnavailable) as ei:
        client(handler).request("GET", "/api/v2/tickets.json")

    message = str(ei.value)
    assert "retry budget" in message
    assert str(_http.MAX_TOTAL_RETRY_SECONDS) in message
    # A caller reading this must be told it was this client giving up, not a further
    # refusal from Zendesk.
    assert "not because Zendesk refused again" in message


# --- path validation: a hostile or malformed `path` must never reach the wire ----
#
# Not reachable while every caller interpolates an int (get_ticket and friends), but
# it becomes reachable the moment a model-supplied path exists (ADR-008's
# zendesk_request), and prompt injection through ticket content is this project's
# named primary risk. Probe-verified live against the pre-fix code: three of the
# five shapes below actually redirected the credentialed request to a different
# host under this client's `f"{base}{path}"` construction; the other two do not
# redirect under that specific construction but are refused anyway, since a path
# shaped like this is hostile regardless of whether today's string concatenation
# happens to defeat it.


@pytest.mark.parametrize(
    "path",
    [
        "@evil.example.net/x",  # tenant host becomes URL userinfo; evil host becomes the host
        ".evil.example.net/x",  # host becomes "<tenant>.zendesk.com.evil.example.net"
        "https://evil.example.net/x",  # host becomes "<tenant>.zendesk.comhttps", still resolvable
        "//evil.example.net/x",  # does not redirect under plain concatenation, refused anyway
        "/api/v2/tickets/1.json@evil.example.net",  # '@' mid-path, refused regardless of position
    ],
)
def test_a_hostile_path_is_refused_before_any_request_is_made(path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={})

    with pytest.raises(exc.InvalidPath):
        client(handler).get(path)
    assert calls["n"] == 0  # the credentialed request must never reach the transport


def test_an_ordinary_path_is_unaffected_by_the_validation():
    def handler(request):
        assert request.url.host == "example.zendesk.com"
        return httpx.Response(200, json={"ok": True})

    assert client(handler).get("/api/v2/tickets/1.json") == {"ok": True}


def test_a_query_string_embedded_in_path_is_refused_not_silently_dropped():
    # Precisely the defect class check_params exists to prevent, arriving through
    # the one parameter check_params cannot see: with a params= dict (even an
    # empty one) also passed to httpx, a '?' embedded in path is dropped rather
    # than sent - so a mixed-pagination query in path would evade check_params
    # entirely were it not refused here first.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={})

    with pytest.raises(exc.InvalidPath):
        client(handler).get("/api/v2/tickets.json?page[after]=abc&sort_by=created_at")
    assert calls["n"] == 0


def test_a_fragment_embedded_in_path_is_also_refused():
    with pytest.raises(exc.InvalidPath):
        client(lambda request: httpx.Response(200, json={})).get("/api/v2/tickets.json#section")


def test_the_built_url_is_verified_against_the_tenant_host_not_just_the_pattern():
    # Belt-and-braces (CLAUDE.md invariant-style reasoning): no path shape has been
    # found that passes _validate_path's patterns and still resolves to a
    # different host under this client's construction, so this exercises the
    # second, independent check directly by making the two disagree - the failure
    # mode is a credential sent to an attacker, and the cost of checking is one
    # comparison per request.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={})

    c = client(handler)
    c._host = "acme.zendesk.com"  # deliberately not "example.zendesk.com", the real tenant host here
    with pytest.raises(exc.InvalidPath):
        c.get("/api/v2/tickets.json")
    assert calls["n"] == 0


def test_a_path_httpx_itself_refuses_to_parse_becomes_a_typed_apierror():
    # httpx.InvalidURL is not an httpx.HTTPError subclass and would otherwise
    # escape this module untyped. This path passes _validate_path (single leading
    # slash, no '@', no '?' or '#') - it fails later, when httpx itself parses the
    # URL and rejects the embedded control character.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={})

    with pytest.raises(exc.ApiError):
        client(handler).get("/api/v2/tickets/\x00.json")
    assert calls["n"] == 0


def test_a_401_with_invalid_token_refreshes_once_and_retries():
    # ADR-009: retried once, and ONLY on invalid_token.
    calls = {"n": 0, "refreshed": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"ok": True})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: CANARY,
        transport=httpx.MockTransport(handler),
        on_invalid_token=lambda: calls.__setitem__("refreshed", calls["refreshed"] + 1),
    )
    assert c.get("/api/v2/tickets.json") == {"ok": True}
    assert calls["refreshed"] == 1
    assert calls["n"] == 2


def test_a_401_from_a_scope_problem_is_passed_through_unchanged():
    # Blanket-retrying 401/403 would mask a scope misconfiguration as a transient
    # fault - and Zendesk issues tokens for unrecognised scope names, so this is
    # the common case, not the rare one.
    refreshed = []

    def handler(request):
        return httpx.Response(401, json={"error": "insufficient_scope"})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: CANARY,
        transport=httpx.MockTransport(handler),
        on_invalid_token=lambda: refreshed.append(1),
    )
    with pytest.raises(exc.CredentialsRejected):
        c.get("/api/v2/tickets.json")
    assert refreshed == []


def test_a_second_invalid_token_is_not_retried_again():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": "invalid_token"})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: CANARY,
        transport=httpx.MockTransport(handler),
        on_invalid_token=lambda: None,
    )
    with pytest.raises(exc.CredentialsRejected):
        c.get("/api/v2/tickets.json")
    assert calls["n"] == 2  # the original and exactly one retry


def test_a_401_with_invalid_token_but_no_on_invalid_token_hook_surfaces_unchanged():
    # A caller that has not wired up access_token() gets today's behaviour: no
    # retry attempted, and the 401 surfaces as CredentialsRejected.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": "invalid_token"})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: CANARY,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(exc.CredentialsRejected):
        c.get("/api/v2/tickets.json")
    assert calls["n"] == 1


def test_a_401_with_a_malformed_body_does_not_crash_the_discriminator():
    # _is_invalid_token must not raise on a body that is not JSON, and must not
    # treat it as invalid_token - the request surfaces as whatever ordinary error
    # parse_error assigns a non-JSON-object body, never a retry.
    refreshed = []

    def handler(request):
        return httpx.Response(401, content=b"not json at all")

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: CANARY,
        transport=httpx.MockTransport(handler),
        on_invalid_token=lambda: refreshed.append(1),
    )
    with pytest.raises(exc.ApiError):
        c.get("/api/v2/tickets.json")
    assert refreshed == []


def test_the_retried_request_is_identical_except_for_the_bearer_token():
    # A retry that rebuilds the request from partial state is a silent
    # correctness bug that only shows up on non-idempotent calls - assert the
    # method, path, query and body are unchanged across the retry, and only the
    # bearer token differs.
    seen: list[dict[str, object]] = []
    tokens = iter(["stale-token", "fresh-token"])

    def handler(request):
        seen.append(
            {
                "method": request.method,
                "url": str(request.url),
                "content": request.content,
                "auth": request.headers.get("authorization", ""),
            }
        )
        if len(seen) == 1:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"ok": True})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: next(tokens),
        transport=httpx.MockTransport(handler),
        on_invalid_token=lambda: None,
    )
    result = c.request("PUT", "/api/v2/tickets/1.json", json={"ticket": {"subject": "hi"}})
    assert result == {"ok": True}
    assert len(seen) == 2
    first, second = seen
    assert first["method"] == second["method"] == "PUT"
    assert first["url"] == second["url"]
    assert first["content"] == second["content"]
    assert first["auth"] == "Bearer stale-token"
    assert second["auth"] == "Bearer fresh-token"


def test_a_token_endpoint_failure_inside_on_invalid_token_names_the_token_endpoint_not_the_ticket_path(
    monkeypatch, tmp_path
):
    # Regression (final review, item 6): a ConnectError raised deep inside
    # `on_invalid_token` (ADR-009's sanctioned use for it is a forced refresh)
    # used to propagate up through `_send`'s own `except (httpx.HTTPError,
    # ...)`, which cannot tell it apart from a failure talking to the ticket
    # endpoint this request was actually for - so it was reported as "could
    # not reach Zendesk ... requesting GET /api/v2/tickets/123.json", naming
    # an endpoint that was never even attempted. `_flow._post` now translates
    # its own httpx errors into a typed `exc.ApiError` naming the OAuth token
    # endpoint before they can reach `_send`'s except clause at all.
    from csa_zendesk.auth import _flow, _store

    monkeypatch.setenv("CSA_ZENDESK_TOKEN_FILE", str(tmp_path / "t.json"))
    _store.write(_store.Tokens("STALE-AT", "RT", 0.0, "read"))

    def oauth_handler(request):
        raise httpx.ConnectError("offline", request=request)

    def force_refresh() -> None:
        stored = _store.read()
        assert stored is not None
        _flow.refresh(
            subdomain="example",
            client_id="cid",
            tokens=stored,
            transport=httpx.MockTransport(oauth_handler),
        )

    def ticket_handler(request):
        return httpx.Response(401, json={"error": "invalid_token"})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: "STALE-AT",
        transport=httpx.MockTransport(ticket_handler),
        on_invalid_token=force_refresh,
    )
    with pytest.raises(exc.ApiError) as ei:
        c.get("/api/v2/tickets/123.json")
    assert "token endpoint" in str(ei.value)
    assert "tickets/123.json" not in str(ei.value)


def test_the_retry_budget_and_invalid_token_retry_survive_the_split():
    # Pins the two behaviours most likely to break when the retry loop moves:
    # a 429 is retried within budget, and an invalid_token 401 refreshes once.
    calls = {"n": 0, "refreshed": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate"})
        if calls["n"] == 2:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"ticket": {"id": 1}})

    c = HttpClient(
        subdomain="example",
        token_provider=lambda: "AT",
        on_invalid_token=lambda: calls.__setitem__("refreshed", calls["refreshed"] + 1),
        transport=httpx.MockTransport(handler),
    )
    assert c.get("/api/v2/tickets/1") == {"ticket": {"id": 1}}
    assert calls["n"] == 3
    assert calls["refreshed"] == 1
