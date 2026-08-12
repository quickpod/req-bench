"""Turn a :class:`Request` into a ready-to-paste code snippet.

Three targets are supported -- a shell ``curl`` command, a Python ``requests``
script, and a browser/Node ``fetch`` call.  The generated text is meant to be
copied out of the GUI or piped from the CLI, so it is self-contained and quotes
everything defensively.
"""

from __future__ import annotations

import json

from .errors import ReqBenchError
from .model import Request

LANGUAGES = ["curl", "python", "javascript"]
_ALIASES = {
    "curl": "curl", "bash": "curl", "sh": "curl",
    "python": "python", "python-requests": "python", "requests": "python", "py": "python",
    "javascript": "javascript", "js": "javascript", "fetch": "javascript", "node": "javascript",
}


def _effective_headers(req):
    """Headers as they go on the wire, including auth and body content-type."""
    headers = dict(req.headers or {})
    if req.auth_type == "bearer" and req.auth:
        headers.setdefault("Authorization", f"Bearer {req.auth}")
    if req.body_type == "json" and req.body not in (None, ""):
        headers.setdefault("Content-Type", "application/json")
    return headers


def _body_string(req):
    """Return the request body serialised as it would be sent, or None."""
    if req.body_type == "none" or req.body in (None, ""):
        return None
    if req.body_type == "json":
        return json.dumps(req.body)
    if req.body_type == "form":
        if not isinstance(req.body, dict):
            raise ReqBenchError("a form body must be a mapping of fields")
        from urllib.parse import urlencode
        return urlencode(req.body)
    return str(req.body)


def _sh_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def _to_curl(req):
    parts = ["curl", "-X", req.method, _sh_quote(req.url)]
    if req.params:
        # curl needs them on the URL; show as --data-urlencode-free query via -G
        from urllib.parse import urlencode
        joiner = "&" if "?" in req.url else "?"
        parts[3] = _sh_quote(req.url + joiner + urlencode(req.params))
    for k, v in _effective_headers(req).items():
        parts += ["-H", _sh_quote(f"{k}: {v}")]
    if req.auth_type == "basic" and isinstance(req.auth, (list, tuple)):
        parts += ["-u", _sh_quote(f"{req.auth[0]}:{req.auth[1]}")]
    body = _body_string(req)
    if body is not None:
        parts += ["--data", _sh_quote(body)]
    if not req.follow_redirects:
        pass
    else:
        parts.insert(1, "-L")
    return " ".join(parts)


def _to_python(req):
    lines = ["import requests", ""]
    headers = _effective_headers(req)
    call_args = [f"    {json.dumps(req.method)}", f"    {json.dumps(req.url)}"]
    if req.params:
        lines.append(f"params = {json.dumps(req.params, indent=4)}")
        call_args.append("    params=params")
    if headers:
        lines.append(f"headers = {json.dumps(headers, indent=4)}")
        call_args.append("    headers=headers")
    if req.body_type == "json" and req.body not in (None, ""):
        lines.append(f"payload = {json.dumps(req.body, indent=4)}")
        call_args.append("    json=payload")
    elif req.body_type in ("form", "raw") and req.body not in (None, ""):
        body = req.body if req.body_type == "form" else _body_string(req)
        lines.append(f"data = {json.dumps(body)}")
        call_args.append("    data=data")
    if req.auth_type == "basic" and isinstance(req.auth, (list, tuple)):
        call_args.append(f"    auth=({json.dumps(req.auth[0])}, {json.dumps(req.auth[1])})")
    if not req.follow_redirects:
        call_args.append("    allow_redirects=False")
    if lines[-1] != "":
        lines.append("")
    lines.append("resp = requests.request(")
    lines.append(",\n".join(call_args))
    lines.append(")")
    lines.append("print(resp.status_code)")
    lines.append("print(resp.text)")
    return "\n".join(lines)


def _to_javascript(req):
    headers = _effective_headers(req)
    if req.auth_type == "basic" and isinstance(req.auth, (list, tuple)):
        import base64
        token = base64.b64encode(f"{req.auth[0]}:{req.auth[1]}".encode()).decode()
        headers = dict(headers)
        headers.setdefault("Authorization", f"Basic {token}")
    url = req.url
    if req.params:
        from urllib.parse import urlencode
        joiner = "&" if "?" in url else "?"
        url = url + joiner + urlencode(req.params)
    opts = {"method": req.method}
    if headers:
        opts["headers"] = headers
    body = _body_string(req)
    lines = [f"const url = {json.dumps(url)};"]
    options_body = json.dumps(opts, indent=2)
    if body is not None:
        # splice the body in as its own line so JSON bodies read naturally
        options_body = options_body[:-2] + ",\n  " + f"body: {json.dumps(body)}\n}}"
    lines.append(f"const options = {options_body};")
    lines.append("")
    lines.append("fetch(url, options)")
    lines.append("  .then((r) => r.text())")
    lines.append("  .then((body) => console.log(body))")
    lines.append("  .catch((err) => console.error(err));")
    return "\n".join(lines)


_GENERATORS = {"curl": _to_curl, "python": _to_python, "javascript": _to_javascript}


def generate(request, language):
    """Return a code snippet for *request* in *language* (curl/python/javascript)."""
    key = _ALIASES.get(str(language).strip().lower())
    if key is None:
        raise ReqBenchError(
            f"unknown language {language!r}; choose from {', '.join(LANGUAGES)}"
        )
    req = Request.from_dict(request)
    if not req.url:
        raise ReqBenchError("cannot generate code for a request with no URL")
    return _GENERATORS[key](req)
