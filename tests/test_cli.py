"""CLI tests: drive reqbench.__main__.main against the local echo server.

HOME is redirected to a temp dir so the CLI's history/collections/environments
write into an isolated store, never the developer's real config.
"""

from __future__ import annotations

import json

import pytest

from reqbench.__main__ import main


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    yield


def test_send_get_prints_status_and_body(echo_server, capsys):
    rc = main(["send", "get", echo_server + "/get"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "200" in out
    assert '"method": "GET"' in out


def test_send_json_body(echo_server, capsys):
    rc = main(["send", "post", echo_server + "/post", "--json", '{"a": 1}'])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"a": 1' in out


def test_send_header_and_query(echo_server, capsys):
    rc = main(["send", "get", echo_server + "/get",
               "-H", "X-Test: yes", "-q", "k=v"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "X-Test" in out and "yes" in out
    assert '"k": "v"' in out


def test_send_bearer_auth(echo_server, capsys):
    rc = main(["send", "get", echo_server + "/get", "--auth", "bearer:tok"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Bearer tok" in out


def test_send_output_to_file(echo_server, tmp_path, capsys):
    dest = tmp_path / "body.json"
    rc = main(["send", "get", echo_server + "/get", "-o", str(dest)])
    assert rc == 0
    assert dest.exists()
    assert json.loads(dest.read_text())["method"] == "GET"


def test_connection_error_exit_code_1(dead_url, capsys):
    rc = main(["send", "get", dead_url, "--timeout", "2"])
    err = capsys.readouterr().err
    assert rc == 1
    assert err.startswith("error:")


def test_graphql_command(echo_server, capsys):
    rc = main(["graphql", echo_server + "/graphql", "--query", "{ ping }",
               "--variables", '{"x": 1}'])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ping" in out


def test_codegen_command(capsys):
    rc = main(["codegen", "curl", "get", "https://api.example.com/x"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("curl")
    assert "https://api.example.com/x" in out


def test_env_setvar_set_and_list(capsys):
    assert main(["env", "setvar", "dev", "base", "http://localhost:8000"]) == 0
    assert main(["env", "set", "dev"]) == 0
    rc = main(["env", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "* dev" in out
    assert "base = http://localhost:8000" in out


def test_env_substitution_in_send(echo_server, capsys):
    main(["env", "setvar", "dev", "host", echo_server])
    main(["env", "set", "dev"])
    capsys.readouterr()
    rc = main(["send", "get", "{{host}}/get", "-H", "X-Env: on"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"method": "GET"' in out


def test_collection_save_run_list(echo_server, capsys):
    assert main(["collection", "save", "Local", "ping",
                 "get", echo_server + "/get"]) == 0
    capsys.readouterr()
    assert main(["collection", "list"]) == 0
    out = capsys.readouterr().out
    assert "Local" in out and "ping" in out

    rc = main(["collection", "run", "Local", "ping"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "200" in out


def test_history_list_and_replay(echo_server, capsys):
    main(["send", "get", echo_server + "/get"])
    capsys.readouterr()
    assert main(["history", "list"]) == 0
    out = capsys.readouterr().out
    assert "GET" in out and "/get" in out

    rc = main(["history", "replay", "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "200" in out


def test_version(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "reqbench" in out
