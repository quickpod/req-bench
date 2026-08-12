"""GraphQL support -- a thin, well-behaved POST over :func:`reqbench.http.send`.

A GraphQL request is just an HTTP POST whose JSON body carries a ``query`` and
optional ``variables``.  Building it here (rather than making the caller hand-
roll the envelope) keeps the GUI's GraphQL tab and the ``graphql`` CLI command
identical, and lets both reuse the same timeout / auth / error handling.
"""

from __future__ import annotations

from .errors import ReqBenchError
from .http import send
from .model import Request


def build_graphql_request(url, query, variables=None, headers=None, **kw):
    """Return the :class:`Request` a GraphQL call sends (no network involved)."""
    if not query or not str(query).strip():
        raise ReqBenchError("a GraphQL request needs a non-empty query")
    body = {"query": query}
    if variables:
        if not isinstance(variables, dict):
            raise ReqBenchError("GraphQL variables must be a mapping")
        body["variables"] = variables
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    hdrs.update(headers or {})
    return Request(
        method="POST",
        url=url,
        headers=hdrs,
        body_type="json",
        body=body,
        auth_type=kw.get("auth_type", "none"),
        auth=kw.get("auth"),
        timeout=kw.get("timeout", 30.0),
        follow_redirects=kw.get("follow_redirects", True),
    )


def send_graphql(url, query, variables=None, headers=None, **kw):
    """Send a GraphQL *query* to *url* and return a :class:`Response`."""
    return send(build_graphql_request(url, query, variables, headers, **kw))
