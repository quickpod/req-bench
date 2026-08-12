"""Collections, environments and {{var}} substitution tests."""

from __future__ import annotations

import pytest

from reqbench import Request, ReqBenchError
from reqbench import collections as col


def test_collection_save_load_round_trip(store):
    req = Request(method="POST", url="https://api/things", body_type="json",
                  body={"a": 1}, headers={"X-K": "v"})
    col.save_request("MyAPI", "create thing", req, base=store)

    assert col.list_collections(base=store) == ["MyAPI"]
    assert col.list_requests("MyAPI", base=store) == ["create thing"]

    loaded = col.load_request("MyAPI", "create thing", base=store)
    assert loaded.method == "POST"
    assert loaded.url == "https://api/things"
    assert loaded.body == {"a": 1}
    assert loaded.headers == {"X-K": "v"}
    assert loaded.name == "create thing"


def test_delete_request_and_collection(store):
    col.save_request("C", "r1", Request(url="http://a"), base=store)
    col.save_request("C", "r2", Request(url="http://b"), base=store)
    assert col.delete_request("C", "r1", base=store) is True
    assert col.list_requests("C", base=store) == ["r2"]
    assert col.delete_collection("C", base=store) is True
    assert col.list_collections(base=store) == []


def test_load_missing_request_raises(store):
    with pytest.raises(ReqBenchError):
        col.load_request("nope", "nope", base=store)


def test_environment_set_and_active(store):
    col.set_env_var("dev", "base", "http://localhost:8000", base=store)
    col.set_env_var("dev", "token", "abc", base=store)
    col.set_env_var("prod", "base", "https://api.example.com", base=store)
    assert col.list_environments(base=store) == ["dev", "prod"]

    col.set_active_environment("dev", base=store)
    assert col.active_environment(base=store) == "dev"
    assert col.get_env_vars(base=store) == {"base": "http://localhost:8000",
                                            "token": "abc"}


def test_var_substitution_from_active_env(store):
    col.set_env_var("dev", "base", "http://localhost:8000", base=store)
    col.set_env_var("dev", "token", "s3cret", base=store)
    col.set_active_environment("dev", base=store)

    req = Request(method="GET", url="{{base}}/users",
                  headers={"Authorization": "Bearer {{token}}"})
    resolved = col.apply_environment(req, base=store)
    assert resolved.url == "http://localhost:8000/users"
    assert resolved.headers["Authorization"] == "Bearer s3cret"


def test_unknown_var_left_intact():
    out = col.substitute("{{missing}}/path", {"other": "x"})
    assert out == "{{missing}}/path"


def test_substitution_is_recursive():
    data = {"url": "{{b}}/x", "list": ["{{b}}", "y"], "n": 3}
    out = col.substitute(data, {"b": "http://h"})
    assert out == {"url": "http://h/x", "list": ["http://h", "y"], "n": 3}


def test_set_active_unknown_env_raises(store):
    with pytest.raises(ReqBenchError):
        col.set_active_environment("ghost", base=store)
