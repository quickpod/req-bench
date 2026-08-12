"""Code-generation tests: snippets must carry the method and URL."""

from __future__ import annotations

import pytest

from reqbench import Request, generate_code, ReqBenchError


def _req():
    return Request(method="POST", url="https://api.example.com/items",
                   headers={"X-Api": "k"}, body_type="json", body={"n": 1},
                   auth_type="bearer", auth="tok")


def test_curl_contains_method_and_url():
    out = generate_code(_req(), "curl")
    assert out.startswith("curl")
    assert "POST" in out
    assert "https://api.example.com/items" in out
    assert "Authorization: Bearer tok" in out


def test_python_contains_method_and_url():
    out = generate_code(_req(), "python")
    assert "import requests" in out
    assert '"POST"' in out
    assert "https://api.example.com/items" in out


def test_javascript_contains_method_and_url():
    out = generate_code(_req(), "js")
    assert "fetch(" in out
    assert '"POST"' in out
    assert "https://api.example.com/items" in out


def test_language_aliases_resolve():
    for alias in ("curl", "bash", "python", "py", "requests", "js", "fetch", "node"):
        assert generate_code(_req(), alias)


def test_unknown_language_raises():
    with pytest.raises(ReqBenchError):
        generate_code(_req(), "cobol")


def test_empty_url_raises():
    with pytest.raises(ReqBenchError):
        generate_code(Request(method="GET", url=""), "curl")
