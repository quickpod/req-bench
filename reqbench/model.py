"""The plain-data :class:`Request` and :class:`Response` records.

Both are simple, dependency-free dataclasses so they serialise cleanly to the
JSON that collections and history store, and so the CLI, GUI and tests all pass
the *same* shape around.  Nothing here touches the network -- see
:mod:`reqbench.http`.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .errors import ReqBenchError

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
BODY_TYPES = ["none", "json", "form", "raw"]
AUTH_TYPES = ["none", "basic", "bearer"]


@dataclass
class Request:
    """Everything needed to send one HTTP request.

    ``body`` is a dict for ``json``/``form`` bodies and a string for ``raw``.
    ``auth`` is ``[user, password]`` for basic auth and a token string for
    bearer auth; it is ignored when ``auth_type`` is ``"none"``.
    """

    method: str = "GET"
    url: str = ""
    headers: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    body_type: str = "none"
    body: Any = None
    auth_type: str = "none"
    auth: Any = None
    timeout: float = 30.0
    follow_redirects: bool = True
    name: str = ""

    def __post_init__(self):
        self.method = (self.method or "GET").upper()
        if self.headers is None:
            self.headers = {}
        if self.params is None:
            self.params = {}
        if self.body_type not in BODY_TYPES:
            self.body_type = "none"
        if self.auth_type not in AUTH_TYPES:
            self.auth_type = "none"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """Build a Request from a (possibly partial/untrusted) dict."""
        if isinstance(data, Request):
            return data
        if not isinstance(data, dict):
            raise ReqBenchError("request must be a mapping of fields")
        allowed = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in allowed}
        return cls(**clean)


@dataclass
class Response:
    """The outcome of a sent request.

    Supports both attribute access (``resp.status``) and item access
    (``resp["status"]``) so it reads either as an object or as the
    ``{status, headers, elapsed, size, text, json?}`` mapping the CLI prints.
    """

    status: int = 0
    reason: str = ""
    url: str = ""
    headers: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    size: int = 0
    text: str = ""
    json: Optional[Any] = None
    request: Optional[dict] = None

    # dict-style access ----------------------------------------------------
    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def get(self, key, default=None):
        return getattr(self, key, default)

    @property
    def ok(self):
        """True for 2xx statuses (a convenience -- non-2xx is not an error)."""
        return 200 <= int(self.status) < 300

    def pretty_body(self):
        """Return the body pretty-printed if it is JSON, else the raw text."""
        if self.json is not None:
            try:
                return _json.dumps(self.json, indent=2, ensure_ascii=False)
            except Exception:
                pass
        return self.text

    def to_dict(self):
        return asdict(self)
