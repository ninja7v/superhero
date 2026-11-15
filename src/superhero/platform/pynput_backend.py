"""Input injection for Windows (SendInput), macOS (Quartz events) and X11 (XTest).

pynput already wraps the three native APIs, so this adapter only translates
names and coordinates. On macOS the OS requires the Accessibility permission
for the hosting app; without it events are silently dropped by the system.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from superhero.platform.base import InputUnavailable, press_chord
from superhero.platform.displays import Display


def accessibility_trusted(prompt: bool = False) -> bool | None:
    """macOS: whether this app may control the computer. ``None`` when it cannot be told.

    Without the permission macOS drops injected events silently, so it has to be checked
    up front. ``prompt=True`` shows the system's own dialog pointing at
    System Settings > Privacy & Security > Accessibility.
    """
    if sys.platform != "darwin":
        return True
    try:
        import ApplicationServices as services  # pyobjc, installed with pynput on macOS

        if prompt:
            return bool(
                services.AXIsProcessTrustedWithOptions({services.kAXTrustedCheckOptionPrompt: True})
            )
        return bool(services.AXIsProcessTrusted())
    except Exception:
        return None


class PynputInput:
    name = "pynput"

    def __init__(self) -> None:
        if accessibility_trusted(prompt=True) is False:
            raise InputUnavailable(
                "macOS has not allowed SUPERHERO to control the computer. Enable it under System "
                "Settings > Privacy & Security > Accessibility, then turn control on again."
            )
        try:
            from pynput import keyboard, mouse
        except Exception as exc:  # pynput raises backend-specific errors at import
            raise InputUnavailable(f"Input injection is unavailable: {exc}") from exc
        self._mouse = mouse.Controller()
        self._keyboard = keyboard.Controller()
        self._buttons = {
            "left": mouse.Button.left,
            "right": mouse.Button.right,
            "middle": mouse.Button.middle,
        }
        key = keyboard.Key
        self._keys = {
            "ctrl": key.ctrl, "alt": key.alt, "shift": key.shift, "super": key.cmd,
            "cmd": key.cmd, "enter": key.enter, "escape": key.esc, "tab": key.tab,
            "space": key.space, "backspace": key.backspace, "delete": key.delete,
            "up": key.up, "down": key.down, "left": key.left, "right": key.right,
            "home": key.home, "end": key.end, "pageup": key.page_up, "pagedown": key.page_down,
            **{f"f{i}": getattr(key, f"f{i}") for i in range(1, 13)},
        }  # fmt: skip

    def move(self, display: Display, nx: float, ny: float) -> None:
        self._mouse.position = display.native_point(nx, ny)

    def button(self, button: str, pressed: bool) -> None:
        (self._mouse.press if pressed else self._mouse.release)(self._buttons[button])

    def scroll(self, lines: int) -> None:
        self._mouse.scroll(0, lines)

    def keys(self, combo: Sequence[str]) -> None:
        resolved = [self._keys.get(name, name) for name in combo]
        press_chord(self._keyboard.press, self._keyboard.release, resolved)

    def type_text(self, text: str) -> None:
        self._keyboard.type(text)

    def close(self) -> None:
        pass
