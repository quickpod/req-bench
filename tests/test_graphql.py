"""GraphQL wrapper tests (against the local echo server)."""

from __future__ import annotations

from reqbench import send_graphql, build_graphql_request, ReqBenchError

import pytest


def test_graphql_posts_query_and_variables(echo_server):
    resp = send_graphql(
        echo_server + "/graphql",
        "query($id: ID!){ user(id:$id){ name } }",
        variables={"id": "7"},
    )
    assert resp.status == 200
    assert resp.json["method"] == "POST"
    assert resp.json["body"]["query"].startswith("query")
    assert resp.json["body"]["variables"] == {"id": "7"}


def test_graphql_request_is_json_post():
    req = build_graphql_request("http://x/graphql", "{ ping }")
    assert req.method == "POST"
    assert req.body_type == "json"
    assert req.body["query"] == "{ ping }"


def test_graphql_empty_query_raises():
    with pytest.raises(ReqBenchError):
        build_graphql_request("http://x/graphql", "")
