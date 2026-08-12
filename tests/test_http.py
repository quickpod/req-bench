"""Round-trip and error tests for reqbench.http.send (no external network)."""

from __future__ import annotations

import base64

import pytest

from reqbench import Request, send, ReqBenchError


def test_get_returns_200_and_parsed_json(echo_server):
    resp = send(Request(method="GET", url=echo_server + "/get"))
    assert resp.status == 200
    assert resp.ok
    assert resp.json is not None
    assert resp.json["method"] == "GET"
    assert resp.elapsed_ms >= 0
    assert resp.size > 0


def test_post_json_body_is_echoed(echo_server):
    payload = {"name": "ada", "n": 42}
    resp = send(Request(method="POST", url=echo_server + "/post",
                        body_type="json", body=payload))
    assert resp.status == 200
    assert resp.json["method"] == "POST"
    assert resp.json["body"] == payload
    assert "application/json" in resp.json["headers"].get("Content-Type", "")


def test_form_body_is_sent_urlencoded(echo_server):
    resp = send(Request(method="POST", url=echo_server + "/post",
                        body_type="form", body={"a": "1", "b": "two"}))
    assert "a=1" in resp.json["raw"]
    assert "b=two" in resp.json["raw"]


def test_raw_body_is_sent_verbatim(echo_server):
    resp = send(Request(method="PUT", url=echo_server + "/put",
                        body_type="raw", body="hello raw"))
    assert resp.json["raw"] == "hello raw"


def test_headers_and_params_applied(echo_server):
    resp = send(Request(method="GET", url=echo_server + "/get",
                        headers={"X-Test": "yes"}, params={"q": "search", "n": "2"}))
    assert resp.json["headers"].get("X-Test") == "yes"
    assert resp.json["args"] == {"q": "search", "n": "2"}


def test_basic_auth_header_set(echo_server):
    resp = send(Request(method="GET", url=echo_server + "/get",
                        auth_type="basic", auth=["user", "pass"]))
    got = resp.json["headers"].get("Authorization", "")
    assert got.startswith("Basic ")
    decoded = base64.b64decode(got.split(" ", 1)[1]).decode()
    assert decoded == "user:pass"


def test_bearer_auth_header_set(echo_server):
    resp = send(Request(method="GET", url=echo_server + "/get",
                        auth_type="bearer", auth="tok123"))
    assert resp.json["headers"].get("Authorization") == "Bearer tok123"


def test_http_error_status_is_not_an_exception(echo_server):
    resp = send(Request(method="GET", url=echo_server + "/status/404"))
    assert resp.status == 404
    assert not resp.ok  # a 404 is a normal response, not a raised error


def test_redirect_followed_by_default(echo_server):
    resp = send(Request(method="GET", url=echo_server + "/redirect"))
    assert resp.status == 200
    assert resp.url.endswith("/get")


def test_redirect_not_followed_when_disabled(echo_server):
    resp = send(Request(method="GET", url=echo_server + "/redirect",
                        follow_redirects=False))
    assert resp.status == 302


def test_connection_error_is_clean_reqbench_error(dead_url):
    # A dead port is refused instantly on Linux but can time out on Windows;
    # either way the failure must surface as a clean ReqBenchError.
    with pytest.raises(ReqBenchError) as exc:
        send(Request(method="GET", url=dead_url, timeout=2))
    msg = str(exc.value).lower()
    assert "connect" in msg or "timed out" in msg


def test_missing_url_raises(echo_server):
    with pytest.raises(ReqBenchError):
        send(Request(method="GET", url=""))


def test_invalid_scheme_raises():
    with pytest.raises(ReqBenchError):
        send(Request(method="GET", url="not-a-url"))
