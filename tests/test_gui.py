"""GUI import + headless-launch tests (no display required)."""

from __future__ import annotations
import pytest
import sys


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows CI has a real display; main() would open a window and block")
def test_import_is_side_effect_free():
    # Importing must not create a Tk root or touch a display.
    from reqbench import gui
    assert hasattr(gui, "main")
    assert hasattr(gui, "build_app")
    # Aura design system: the per-app accent replaces the old PALETTES dict.
    assert gui.ACCENT.startswith("#") and len(gui.ACCENT) == 7


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows CI has a real display; main() would open a window and block")
def test_main_headless_returns_zero(monkeypatch):
    from reqbench import gui
    # Force the no-display path deterministically.
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(gui.sys, "platform", "linux")
    monkeypatch.setattr(gui.os, "name", "posix")
    assert gui.main() == 0


def test_asset_path_returns_none_or_path():
    from reqbench import gui
    # Never raises; returns None for a missing asset.
    assert gui.asset_path("definitely-not-here.zzz") is None
