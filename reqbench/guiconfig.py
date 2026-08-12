r"""Tiny JSON-backed config + shared config directory for ReqBench.

Stores the chosen theme ("light"/"dark") and a short recents list, and exposes
the directory that collections, environments and history are saved under.  On
Windows the file lives at ``%LOCALAPPDATA%\ReqBench\config.json``; elsewhere it
falls back to ``~/.reqbench/config.json``.  Every function is defensive -- a
corrupt or unreadable config must never stop the app from starting.
"""

from __future__ import annotations

import json
import os

APP_DIRNAME = "ReqBench"
CONFIG_NAME = "config.json"
MAX_RECENT = 12
VALID_THEMES = ("light", "dark")


def config_dir():
    r"""Directory that holds ReqBench's data (created on demand).

    ``%LOCALAPPDATA%\ReqBench`` on Windows, ``~/.reqbench`` otherwise.
    """
    local = os.environ.get("LOCALAPPDATA")
    if local and os.name == "nt":
        return os.path.join(local, APP_DIRNAME)
    return os.path.join(os.path.expanduser("~"), "." + APP_DIRNAME.lower())


def config_path():
    return os.path.join(config_dir(), CONFIG_NAME)


def _defaults():
    return {"theme": "dark", "recent": []}


def load():
    """Return the config dict, always with ``theme`` and ``recent`` keys."""
    cfg = _defaults()
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            theme = data.get("theme")
            if theme in VALID_THEMES:
                cfg["theme"] = theme
            recent = data.get("recent")
            if isinstance(recent, list):
                cfg["recent"] = [p for p in recent if isinstance(p, str)][:MAX_RECENT]
    except Exception:
        pass  # missing/corrupt -> defaults; never fatal
    return cfg


def save(cfg):
    """Persist *cfg* (best-effort; failures are swallowed)."""
    try:
        os.makedirs(config_dir(), exist_ok=True)
        clean = {
            "theme": cfg.get("theme") if cfg.get("theme") in VALID_THEMES else "dark",
            "recent": [p for p in cfg.get("recent", []) if isinstance(p, str)][:MAX_RECENT],
        }
        tmp = config_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
        os.replace(tmp, config_path())
    except Exception:
        pass


def get_theme():
    return load().get("theme", "dark")


def set_theme(theme):
    if theme not in VALID_THEMES:
        return
    cfg = load()
    cfg["theme"] = theme
    save(cfg)


def get_recent():
    return load().get("recent", [])


def add_recent(item):
    """Push *item* (a URL string) to the front of the recent list."""
    if not item:
        return
    cfg = load()
    recent = [p for p in cfg.get("recent", []) if p != item]
    recent.insert(0, item)
    cfg["recent"] = recent[:MAX_RECENT]
    save(cfg)


def clear_recent():
    cfg = load()
    cfg["recent"] = []
    save(cfg)
