"""History append / list / replay tests (round-trips via the echo server)."""

from __future__ import annotations

from reqbench import Request, send
from reqbench import history as hist


def test_append_and_list(store, echo_server):
    req = Request(method="GET", url=echo_server + "/get")
    resp = send(req)
    hist.append(req, resp, base=store)

    entries = hist.list_entries(base=store)
    assert len(entries) == 1
    assert entries[0]["method"] == "GET"
    assert entries[0]["status"] == 200
    assert entries[0]["url"].endswith("/get")


def test_list_is_newest_first(store, echo_server):
    for path in ("/a", "/b", "/c"):
        req = Request(method="GET", url=echo_server + path)
        hist.append(req, send(req), base=store)
    entries = hist.list_entries(base=store)
    assert [e["url"].rsplit("/", 1)[1] for e in entries] == ["c", "b", "a"]


def test_replay_resends_and_records(store, echo_server):
    req = Request(method="POST", url=echo_server + "/post",
                  body_type="json", body={"replayed": True})
    hist.append(req, send(req), base=store)

    resp = hist.replay(0, base=store)
    assert resp.status == 200
    assert resp.json["body"] == {"replayed": True}
    # replay records a fresh entry
    assert len(hist.list_entries(base=store)) == 2


def test_clear(store, echo_server):
    req = Request(method="GET", url=echo_server + "/get")
    hist.append(req, send(req), base=store)
    hist.clear(base=store)
    assert hist.list_entries(base=store) == []
