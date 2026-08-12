"""Request history -- a rolling log of what was sent, for review and replay.

Every send appends a compact summary (method, URL, status, timing, size) plus
the full request payload, so any past call can be *replayed* byte-for-byte.  The
log is a JSON list at ``<config_dir>/history.json``, newest last, capped so it
never grows without bound.  As with collections, a ``base`` directory can be
injected for tests.
"""

from __future__ import annotations

import json
import os
import time

from . import guiconfig
from .errors import ReqBenchError
from .http import send
from .model import Request, Response

HISTORY_FILE = "history.json"
MAX_ENTRIES = 500


def _path(base=None):
    root = base if base else guiconfig.config_dir()
    return os.path.join(root, HISTORY_FILE)


def load(base=None):
    """Return the history list (newest last); ``[]`` when there is none."""
    try:
        with open(_path(base), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001
        raise ReqBenchError(f"could not read history: {exc}") from exc


def _write(entries, base=None):
    try:
        path = _path(base)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries[-MAX_ENTRIES:], fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        raise ReqBenchError(f"could not write history: {exc}") from exc


def append(request, response, base=None):
    """Record that *request* was sent and got *response*; return the entry."""
    req = Request.from_dict(request)
    status = response.status if isinstance(response, Response) else response.get("status")
    elapsed = (response.elapsed_ms if isinstance(response, Response)
               else response.get("elapsed_ms", 0))
    size = response.size if isinstance(response, Response) else response.get("size", 0)
    entry = {
        "time": time.time(),
        "method": req.method,
        "url": req.url,
        "status": status,
        "elapsed_ms": elapsed,
        "size": size,
        "request": req.to_dict(),
    }
    entries = load(base)
    entries.append(entry)
    _write(entries, base)
    return entry


def list_entries(base=None, limit=None):
    """Return history entries newest-first (optionally capped to *limit*)."""
    entries = list(reversed(load(base)))
    return entries[:limit] if limit else entries


def clear(base=None):
    _write([], base)


def replay(index, base=None):
    """Re-send the request at *index* of the newest-first list; append the result."""
    entries = list_entries(base)
    if not entries:
        raise ReqBenchError("history is empty -- nothing to replay")
    try:
        entry = entries[index]
    except (IndexError, TypeError):
        raise ReqBenchError(f"no history entry at position {index}") from None
    req = Request.from_dict(entry.get("request") or {})
    resp = send(req)
    append(req, resp, base)
    return resp
