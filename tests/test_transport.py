import pathlib

import httpx
import pytest

from csa_zendesk._http import HttpClient
from csa_zendesk._transport import Transport

CANARY = "zdt-CANARY-8f2a1c-DO-NOT-LOG"


def client(handler, **kw):
    return HttpClient(subdomain="example", token_provider=lambda: CANARY, transport=httpx.MockTransport(handler), **kw)


def test_post_binary_sends_the_raw_body_and_content_type_and_returns_the_envelope():
    seen = {}

    def handler(request):
        seen["content"] = request.content
        seen["content_type"] = request.headers.get("content-type")
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"upload": {"token": "abc"}})

    result = client(handler).post_binary(
        "/api/v2/uploads.json",
        content=b"\x00binary-bytes",
        content_type="application/binary",
        params={"filename": "screenshot.png"},
    )
    assert result == {"upload": {"token": "abc"}}
    assert seen["content"] == b"\x00binary-bytes"
    assert seen["content_type"] == "application/binary"
    assert seen["params"] == {"filename": "screenshot.png"}


def test_post_binary_without_params_omits_the_query_string():
    def handler(request):
        assert dict(request.url.params) == {}
        return httpx.Response(204)

    assert client(handler).post_binary("/api/v2/uploads.json", content=b"x", content_type="text/plain") == {}


def test_send_refuses_json_and_content_together():
    # Guards the one caller mistake that would otherwise silently pick a body:
    # httpx.Client.build_request() would just prefer one of the two, and nothing
    # would ever tell the caller the request was ambiguous.
    t = Transport(token_provider=lambda: CANARY, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(ValueError, match="not both"):
        t.send(
            "POST",
            "/api/v2/tickets.json",
            host="example.zendesk.com",
            base="https://example.zendesk.com",
            json={"ticket": {}},
            content=b"x",
        )


def test_transport_module_has_no_api_path_or_envelope_knowledge():
    # The seam test named in the task brief: if this module ever contains a
    # literal "/api/v2" or starts inspecting what a Zendesk envelope looks like,
    # the split has drifted and pagination/rate-limit work has nowhere clean to
    # land. Checked against the source text directly, not by behaviour, because
    # the property being guarded is "what this module knows", not "what it does".
    source = pathlib.Path(__file__).parent.parent.joinpath("src", "csa_zendesk", "_transport.py").read_text()
    assert "/api/v2" not in source
