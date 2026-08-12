"""The one place reqbench actually talks to the network.

:func:`send` wraps ``requests`` and normalises everything into a
:class:`~reqbench.model.Response`.  Two rules matter:

* A normal HTTP *status* (404, 500, a redirect that is not followed, ...) is
  **never** an error -- it is returned like any other response.
* Only a genuine transport failure (no connection, DNS failure, timeout, an
  invalid URL) raises, and it raises a :class:`ReqBenchError` carrying a short,
  human-readable message -- never a ``requests`` traceback.
"""

from __future__ import annotations

import json as _jsonlib

from .errors import ReqBenchError
from .model import Request, Response


def _load_requests():
    """Import ``requests`` lazily so importing this module never fails."""
    try:
        import requests  # noqa: PLC0415 - deliberate lazy import
        return requests
    except Exception as exc:  # pragma: no cover - requests is a declared dep
        raise ReqBenchError(
            "the 'requests' library is required to send requests "
            f"({exc}); install it with: pip install requests"
        ) from exc


def _auth_for(req, requests):
    """Translate a Request's auth fields into requests' auth/header inputs."""
    if req.auth_type == "basic":
        cred = req.auth
        if isinstance(cred, (list, tuple)) and len(cred) == 2:
            return tuple(cred), None
        raise ReqBenchError("basic auth needs a [username, password] pair")
    if req.auth_type == "bearer":
        token = req.auth
        if not isinstance(token, str) or not token:
            raise ReqBenchError("bearer auth needs a non-empty token string")
        return None, {"Authorization": f"Bearer {token}"}
    return None, None


def _body_kwargs(req):
    """Return the requests kwargs (json=/data=) and any extra headers a body needs."""
    if req.body_type == "none" or req.body in (None, ""):
        return {}, {}
    if req.body_type == "json":
        return {"json": req.body}, {}
    if req.body_type == "form":
        if not isinstance(req.body, dict):
            raise ReqBenchError("a form body must be a mapping of fields")
        return {"data": req.body}, {}
    if req.body_type == "raw":
        data = req.body if isinstance(req.body, (str, bytes)) else str(req.body)
        return {"data": data}, {}
    raise ReqBenchError(f"unknown body type: {req.body_type!r}")


def send(request):
    """Send *request* and return a :class:`Response`.

    *request* may be a :class:`Request` or a plain dict of the same fields.
    """
    req = Request.from_dict(request)
    if not req.url or not str(req.url).strip():
        raise ReqBenchError("a request needs a URL")

    requests = _load_requests()

    headers = dict(req.headers or {})
    auth, auth_headers = _auth_for(req, requests)
    if auth_headers:
        headers.update(auth_headers)
    body_kwargs, body_headers = _body_kwargs(req)
    headers.update(body_headers)

    try:
        timeout = float(req.timeout) if req.timeout else None
    except (TypeError, ValueError):
        timeout = None

    try:
        resp = requests.request(
            req.method,
            req.url,
            headers=headers or None,
            params=req.params or None,
            auth=auth,
            timeout=timeout,
            allow_redirects=bool(req.follow_redirects),
            **body_kwargs,
        )
    except requests.exceptions.Timeout:
        raise ReqBenchError(
            f"request timed out after {req.timeout}s: {req.url}"
        ) from None
    except requests.exceptions.SSLError as exc:
        raise ReqBenchError(f"SSL error contacting {req.url}: {exc}") from None
    except requests.exceptions.ConnectionError:
        raise ReqBenchError(
            f"could not connect to {req.url} -- is the host reachable?"
        ) from None
    except requests.exceptions.MissingSchema:
        raise ReqBenchError(
            f"invalid URL {req.url!r} -- did you mean http:// or https://?"
        ) from None
    except requests.exceptions.InvalidURL:
        raise ReqBenchError(f"invalid URL: {req.url!r}") from None
    except requests.exceptions.RequestException as exc:
        raise ReqBenchError(f"request failed: {exc}") from None

    text = resp.text
    parsed = None
    ctype = resp.headers.get("Content-Type", "")
    if "json" in ctype.lower():
        try:
            parsed = resp.json()
        except (ValueError, _jsonlib.JSONDecodeError):
            parsed = None
    elif text:
        # Some servers omit the header; try a cheap parse when it looks like JSON.
        stripped = text.lstrip()
        if stripped[:1] in "{[":
            try:
                parsed = _jsonlib.loads(text)
            except ValueError:
                parsed = None

    try:
        size = len(resp.content)
    except Exception:
        size = len(text.encode("utf-8", "replace"))

    return Response(
        status=resp.status_code,
        reason=resp.reason or "",
        url=resp.url,
        headers=dict(resp.headers),
        elapsed_ms=round(resp.elapsed.total_seconds() * 1000.0, 2),
        size=size,
        text=text,
        json=parsed,
        request=req.to_dict(),
    )
