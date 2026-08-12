"""Command-line interface: ``python -m reqbench <command> ...``.

Mirrors the GUI's feature set from a terminal: send REST/GraphQL requests, run
saved requests from a collection under the active environment, manage
environment variables, and print client-code snippets.  Every command prints a
short status/timing header and the body; ``--output`` writes the body to a file
instead of stdout.  A :class:`ReqBenchError` becomes a clean ``error: ...`` line
and exit code 1 -- never a traceback.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import collections as col
from . import history as hist
from .codegen import generate, LANGUAGES
from .errors import ReqBenchError
from .graphql import build_graphql_request
from .http import send
from .model import Request, METHODS


# --- request building from CLI flags ---------------------------------------
def _parse_headers(items):
    out = {}
    for raw in items or []:
        if ":" not in raw:
            raise ReqBenchError(f"bad --header {raw!r}; use 'Name: value'")
        k, v = raw.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _parse_queries(items):
    out = {}
    for raw in items or []:
        if "=" not in raw:
            raise ReqBenchError(f"bad --query {raw!r}; use 'key=value'")
        k, v = raw.split("=", 1)
        out[k.strip()] = v
    return out


def _parse_auth(spec):
    """``basic:user:pass`` or ``bearer:token`` -> (auth_type, auth)."""
    if not spec:
        return "none", None
    kind, _, rest = spec.partition(":")
    kind = kind.strip().lower()
    if kind == "basic":
        if ":" not in rest:
            raise ReqBenchError("basic auth needs 'basic:user:password'")
        user, _, pw = rest.partition(":")
        return "basic", [user, pw]
    if kind == "bearer":
        if not rest:
            raise ReqBenchError("bearer auth needs 'bearer:token'")
        return "bearer", rest
    raise ReqBenchError("--auth must start with 'basic:' or 'bearer:'")


def _build_request(a):
    body_type, body = "none", None
    if getattr(a, "json", None) is not None:
        try:
            body = json.loads(a.json)
        except ValueError as exc:
            raise ReqBenchError(f"--json is not valid JSON: {exc}") from None
        body_type = "json"
    elif getattr(a, "data", None) is not None:
        body, body_type = a.data, "raw"
    auth_type, auth = _parse_auth(getattr(a, "auth", None))
    return Request(
        method=a.method,
        url=a.url,
        headers=_parse_headers(getattr(a, "header", None)),
        params=_parse_queries(getattr(a, "query", None)),
        body_type=body_type,
        body=body,
        auth_type=auth_type,
        auth=auth,
        timeout=getattr(a, "timeout", 30.0),
        follow_redirects=not getattr(a, "no_redirect", False),
    )


# --- output helpers ---------------------------------------------------------
def _emit_response(resp, output=None, record=True):
    print(f"{resp.status} {resp.reason}  {resp.elapsed_ms:.0f} ms  {resp.size} bytes")
    if record:
        try:
            hist.append(Request.from_dict(resp.request or {}), resp)
        except ReqBenchError:
            pass  # a broken history file must never fail a send
    body = resp.pretty_body()
    if output:
        try:
            with open(output, "w", encoding="utf-8") as fh:
                fh.write(resp.text)
        except OSError as exc:
            raise ReqBenchError(f"could not write {output}: {exc}") from None
        print(f"(body (raw) written to {output})")
    else:
        if body:
            print()
            print(body)


# --- command handlers -------------------------------------------------------
def cmd_send(a):
    req = col.apply_environment(_build_request(a))
    _emit_response(send(req), a.output)


def cmd_graphql(a):
    variables = {}
    if a.variables:
        try:
            variables = json.loads(a.variables)
        except ValueError as exc:
            raise ReqBenchError(f"--variables is not valid JSON: {exc}") from None
    auth_type, auth = _parse_auth(a.auth)
    req = build_graphql_request(
        a.url, a.query, variables, _parse_headers(a.header),
        auth_type=auth_type, auth=auth, timeout=a.timeout,
    )
    _emit_response(send(col.apply_environment(req)), a.output)


def cmd_collection(a):
    if a.action == "list":
        cols = col.list_collections()
        if not cols:
            print("(no collections saved yet)")
            return
        for name in cols:
            print(name)
            for rn in col.list_requests(name):
                req = col.load_request(name, rn)
                print(f"    {rn}: {req.method} {req.url}")
        return
    if a.action == "run":
        req = col.apply_environment(col.load_request(a.collection, a.name))
        _emit_response(send(req), a.output)
        return
    if a.action == "save":
        req = _build_request(a)
        col.save_request(a.collection, a.name, req)
        print(f"saved {a.name!r} to collection {a.collection!r}")
        return


def cmd_env(a):
    if a.action == "list":
        active = col.active_environment()
        envs = col.list_environments()
        if not envs:
            print("(no environments defined)")
            return
        for name in envs:
            mark = "*" if name == active else " "
            print(f"{mark} {name}")
            for k, v in sorted(col.get_env_vars(name).items()):
                print(f"      {k} = {v}")
        return
    if a.action == "set":
        col.set_active_environment(a.name)
        print(f"active environment: {a.name or '(none)'}")
        return
    if a.action == "setvar":
        col.set_env_var(a.env, a.key, a.value)
        print(f"{a.env}: {a.key} = {a.value}")
        return


def cmd_codegen(a):
    req = _build_request(a)
    snippet = generate(req, a.language)
    if a.output:
        try:
            with open(a.output, "w", encoding="utf-8") as fh:
                fh.write(snippet + "\n")
        except OSError as exc:
            raise ReqBenchError(f"could not write {a.output}: {exc}") from None
        print(f"(snippet written to {a.output})")
    else:
        print(snippet)


def cmd_history(a):
    if a.action == "list":
        entries = hist.list_entries(limit=a.limit)
        if not entries:
            print("(history is empty)")
            return
        for i, e in enumerate(entries):
            print(f"{i:3}  {e.get('status'):>3} {e.get('method'):<6} {e.get('url')}")
        return
    if a.action == "replay":
        _emit_response(hist.replay(a.index), a.output, record=False)
        return
    if a.action == "clear":
        hist.clear()
        print("history cleared")
        return


# --- argument parser --------------------------------------------------------
def _add_request_args(sp, with_method_url=True):
    if with_method_url:
        sp.add_argument("method", choices=[m.lower() for m in METHODS] + METHODS,
                        metavar="METHOD", help="GET, POST, ...")
        sp.add_argument("url")
    sp.add_argument("-H", "--header", action="append", metavar="NAME: VALUE",
                    help="request header (repeatable)")
    sp.add_argument("-q", "--query", action="append", metavar="KEY=VALUE",
                    help="query-string param (repeatable)")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--data", metavar="BODY", help="raw request body")
    g.add_argument("--json", metavar="JSON", help="JSON request body (parsed)")
    sp.add_argument("--auth", metavar="SPEC",
                    help="basic:user:pass  or  bearer:token")
    sp.add_argument("--timeout", type=float, default=30.0,
                    help="seconds before giving up (default 30)")
    sp.add_argument("--no-redirect", action="store_true",
                    help="do not follow 3xx redirects")


def build_parser():
    p = argparse.ArgumentParser(
        prog="reqbench",
        description="Offline REST & GraphQL client. Nothing is uploaded anywhere.",
    )
    p.add_argument("--version", action="store_true", help="print version and exit")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("send", help="send an HTTP request")
    _add_request_args(s)
    s.add_argument("-o", "--output", help="write the response body to a file")
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("graphql", help="send a GraphQL query")
    s.add_argument("url")
    s.add_argument("--query", required=True, help="the GraphQL query/mutation")
    s.add_argument("--variables", help="variables as a JSON object")
    s.add_argument("-H", "--header", action="append", metavar="NAME: VALUE")
    s.add_argument("--auth", metavar="SPEC", help="basic:user:pass or bearer:token")
    s.add_argument("--timeout", type=float, default=30.0)
    s.add_argument("-o", "--output", help="write the response body to a file")
    s.set_defaults(func=cmd_graphql)

    s = sub.add_parser("collection", help="list / run / save collection requests")
    csub = s.add_subparsers(dest="action", required=True)
    csub.add_parser("list", help="list collections and their requests")
    r = csub.add_parser("run", help="send a saved request")
    r.add_argument("collection")
    r.add_argument("name")
    r.add_argument("-o", "--output")
    sv = csub.add_parser("save", help="save a request into a collection")
    sv.add_argument("collection")
    sv.add_argument("name")
    _add_request_args(sv)
    s.set_defaults(func=cmd_collection)

    s = sub.add_parser("env", help="list environments / set active / set a variable")
    esub = s.add_subparsers(dest="action", required=True)
    esub.add_parser("list", help="list environments and variables")
    st = esub.add_parser("set", help="choose the active environment")
    st.add_argument("name", nargs="?", default="", help="environment (blank = none)")
    sv = esub.add_parser("setvar", help="set a variable in an environment")
    sv.add_argument("env")
    sv.add_argument("key")
    sv.add_argument("value")
    s.set_defaults(func=cmd_env)

    s = sub.add_parser("codegen", help="print a code snippet for a request")
    s.add_argument("language", choices=LANGUAGES, help="curl | python | javascript")
    _add_request_args(s)
    s.add_argument("-o", "--output", help="write the snippet to a file")
    s.set_defaults(func=cmd_codegen)

    s = sub.add_parser("history", help="list / replay / clear request history")
    hsub = s.add_subparsers(dest="action", required=True)
    hl = hsub.add_parser("list", help="list recent requests")
    hl.add_argument("--limit", type=int, default=20)
    hr = hsub.add_parser("replay", help="re-send a history entry by index")
    hr.add_argument("index", type=int)
    hr.add_argument("-o", "--output")
    hsub.add_parser("clear", help="empty the history")
    s.set_defaults(func=cmd_history)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "version", False):
        from . import __version__
        print(f"reqbench {__version__}")
        return 0
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    # normalise a lower-case method verb
    if hasattr(args, "method") and isinstance(args.method, str):
        args.method = args.method.upper()
    try:
        args.func(args)
    except ReqBenchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
