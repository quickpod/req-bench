"""reqbench -- a permissively-licensed, fully-offline REST & GraphQL client.

The public API is small and everything raises :class:`ReqBenchError` (and only
that) on failure::

    from reqbench import Request, send, send_graphql
    resp = send(Request(method="GET", url="https://example.com"))
    print(resp.status, resp.elapsed_ms, resp.pretty_body())

A normal HTTP status (404, 500, ...) is never an error -- it comes back as a
:class:`~reqbench.model.Response`.  The CLI (:mod:`reqbench.__main__`) and the
tkinter GUI (:mod:`reqbench.gui`) both build on exactly these functions.
"""

from __future__ import annotations

from .errors import ReqBenchError
from .model import Request, Response, METHODS, BODY_TYPES, AUTH_TYPES
from .http import send
from .graphql import send_graphql, build_graphql_request
from .codegen import generate as generate_code, LANGUAGES
from . import collections, history

__version__ = "1.0.6"

__all__ = [
    "ReqBenchError",
    "Request",
    "Response",
    "METHODS",
    "BODY_TYPES",
    "AUTH_TYPES",
    "send",
    "send_graphql",
    "build_graphql_request",
    "generate_code",
    "LANGUAGES",
    "collections",
    "history",
    "__version__",
]
