"""Collections and Environments -- the saved-request store.

A *collection* is a named folder of named requests.  An *environment* is a named
set of ``{{variable}}`` values (base URLs, tokens, ...); one environment is
"active" at a time and its values are substituted into a request just before it
is sent.  Everything is stored as plain JSON in the ReqBench config directory:

    <config_dir>/collections.json     {collection: {request-name: request-dict}}
    <config_dir>/environments.json    {"active": name, "vars": {env: {k: v}}}

Every function accepts an optional ``base`` directory so tests (and anyone who
wants an isolated store) can point it at a temp folder instead of the user's
real config.
"""

from __future__ import annotations

import json
import os
import re

from . import guiconfig
from .errors import ReqBenchError
from .model import Request

COLLECTIONS_FILE = "collections.json"
ENVIRONMENTS_FILE = "environments.json"
_VAR_RE = re.compile(r"\{\{\s*([^}\s]+)\s*\}\}")


# --- paths ------------------------------------------------------------------
def store_dir(base=None):
    return base if base else guiconfig.config_dir()


def _collections_path(base=None):
    return os.path.join(store_dir(base), COLLECTIONS_FILE)


def _environments_path(base=None):
    return os.path.join(store_dir(base), ENVIRONMENTS_FILE)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, type(default)) else default
    except FileNotFoundError:
        return default
    except Exception as exc:  # noqa: BLE001 - normalise to our error
        raise ReqBenchError(f"could not read {os.path.basename(path)}: {exc}") from exc


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        raise ReqBenchError(f"could not write {os.path.basename(path)}: {exc}") from exc


# --- collections ------------------------------------------------------------
def load_collections(base=None):
    """Return ``{collection-name: {request-name: request-dict}}``."""
    return _read_json(_collections_path(base), {})


def save_collections(data, base=None):
    _write_json(_collections_path(base), data)


def list_collections(base=None):
    return sorted(load_collections(base).keys())


def list_requests(collection, base=None):
    cols = load_collections(base)
    if collection not in cols:
        raise ReqBenchError(f"no such collection: {collection!r}")
    return sorted(cols[collection].keys())


def save_request(collection, name, request, base=None):
    """Store *request* (a Request or dict) as *name* inside *collection*."""
    if not collection or not name:
        raise ReqBenchError("a saved request needs a collection and a name")
    req = Request.from_dict(request)
    req.name = name
    cols = load_collections(base)
    cols.setdefault(collection, {})[name] = req.to_dict()
    save_collections(cols, base)
    return req


def load_request(collection, name, base=None):
    """Return the :class:`Request` saved as *name* in *collection*."""
    cols = load_collections(base)
    if collection not in cols:
        raise ReqBenchError(f"no such collection: {collection!r}")
    if name not in cols[collection]:
        raise ReqBenchError(f"no request {name!r} in collection {collection!r}")
    return Request.from_dict(cols[collection][name])


def delete_request(collection, name, base=None):
    cols = load_collections(base)
    if collection in cols and name in cols[collection]:
        del cols[collection][name]
        if not cols[collection]:
            del cols[collection]
        save_collections(cols, base)
        return True
    return False


def delete_collection(collection, base=None):
    cols = load_collections(base)
    if collection in cols:
        del cols[collection]
        save_collections(cols, base)
        return True
    return False


# --- environments -----------------------------------------------------------
def _load_env_store(base=None):
    data = _read_json(_environments_path(base), {})
    if "vars" not in data or not isinstance(data.get("vars"), dict):
        data["vars"] = {}
    if "active" not in data:
        data["active"] = ""
    return data


def load_environments(base=None):
    return _load_env_store(base)


def list_environments(base=None):
    return sorted(_load_env_store(base)["vars"].keys())


def active_environment(base=None):
    return _load_env_store(base).get("active", "")


def set_active_environment(name, base=None):
    """Make *name* the active environment (``""`` selects none)."""
    data = _load_env_store(base)
    if name and name not in data["vars"]:
        raise ReqBenchError(f"no such environment: {name!r}")
    data["active"] = name or ""
    _write_json(_environments_path(base), data)


def set_env_var(env, key, value, base=None):
    if not env or not key:
        raise ReqBenchError("setting a variable needs an environment and a key")
    data = _load_env_store(base)
    data["vars"].setdefault(env, {})[key] = value
    _write_json(_environments_path(base), data)


def get_env_vars(env=None, base=None):
    """Return the variable map for *env* (or the active one when omitted)."""
    data = _load_env_store(base)
    name = env if env is not None else data.get("active", "")
    return dict(data["vars"].get(name, {})) if name else {}


def delete_environment(env, base=None):
    data = _load_env_store(base)
    if env in data["vars"]:
        del data["vars"][env]
        if data.get("active") == env:
            data["active"] = ""
        _write_json(_environments_path(base), data)
        return True
    return False


# --- {{variable}} substitution ---------------------------------------------
def substitute(value, variables):
    """Recursively replace ``{{var}}`` tokens in strings/dicts/lists.

    Unknown variables are left untouched, so a half-configured request still
    shows the operator exactly which placeholder is missing.
    """
    if isinstance(value, str):
        return _VAR_RE.sub(
            lambda m: str(variables.get(m.group(1), m.group(0))), value
        )
    if isinstance(value, dict):
        return {substitute(k, variables): substitute(v, variables)
                for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    return value


def apply_environment(request, variables=None, base=None):
    """Return a copy of *request* with ``{{var}}`` filled from *variables*.

    When *variables* is None the active environment's values are used.
    """
    req = Request.from_dict(request)
    vs = variables if variables is not None else get_env_vars(base=base)
    if not vs:
        return req
    data = substitute(req.to_dict(), vs)
    return Request.from_dict(data)
