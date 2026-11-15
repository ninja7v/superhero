"""The emergency-stop global shortcut."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from superhero.platform.base import session_type

_PYNPUT_NAMES = {"escape": "esc", "super": "cmd", "pageup": "page_up", "pagedown": "page_down"}


class Hotkey(Protocol):
    def stop(self) -> None: ...


def pynput_combo(combo: str) -> str:
    """``ctrl+alt+escape`` -> pynput's ``<ctrl>+<alt>+<esc>``."""
    parts = []
    for part in combo.lower().split("+"):
        name = _PYNPUT_NAMES.get(part, part)
        parts.append(name if len(name) == 1 else f"<{name}>")
    return "+".join(parts)


def register_hotkey(combo: str, callback: Callable[[], None]) -> Hotkey:
    """Register a system-wide shortcut. Raises if the platform refuses."""
    if session_type() == "wayland":
        from superhero.platform.portal import PortalHotkey

        return PortalHotkey(combo, callback)
    from pynput import keyboard

    listener = keyboard.GlobalHotKeys({pynput_combo(combo): callback})
    listener.start()
    return listener
