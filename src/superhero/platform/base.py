"""The input-injection interface every platform adapter implements."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Protocol

from superhero.platform.displays import Display

# Canonical key names used by commands and adapters; anything else is a single character.
KEY_NAMES = (
    "ctrl", "alt", "shift", "super", "cmd", "enter", "escape", "tab", "space", "backspace",
    "delete", "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    *(f"f{i}" for i in range(1, 13)),
)  # fmt: skip


# The application's identity: the desktop entry name, the macOS bundle id, the portal app id.
APP_ID = "io.github.superhero.Superhero"


def desktop_entry_path() -> Path:
    """Where ``superhero install-desktop`` puts the launcher entry on Linux."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "applications" / f"{APP_ID}.desktop"


def press_chord[K](
    press: Callable[[K], None], release: Callable[[K], None], keys: Iterable[K]
) -> None:
    """Press ``keys`` in order, then release them in reverse.

    The releases run even when a press fails part-way, so a failed chord can never leave
    a modifier held down system-wide. The first error is re-raised afterwards.
    """
    held: list[K] = []
    try:
        for key in keys:
            press(key)
            held.append(key)
    finally:
        failure: Exception | None = None
        for key in reversed(held):
            try:
                release(key)
            except Exception as exc:  # keep releasing the others
                failure = failure or exc
        if failure is not None:
            raise failure


class InputUnavailable(RuntimeError):
    """The OS refused or cannot provide input injection (permission, missing service)."""


class InputBackend(Protocol):
    name: str

    def move(self, display: Display, nx: float, ny: float) -> None:
        """Move the system pointer to a display-normalized position."""

    def button(self, button: str, pressed: bool) -> None:
        """Press or release ``"left"`` / ``"right"`` / ``"middle"``."""

    def scroll(self, lines: int) -> None:
        """Scroll vertically; positive is up."""

    def keys(self, combo: Sequence[str]) -> None:
        """Press a chord in order, then release it in reverse."""

    def type_text(self, text: str) -> None:
        """Type text into the focused control."""

    def close(self) -> None: ...


def session_type() -> str:
    """``wayland`` / ``x11`` on Linux, else the platform name."""
    if sys.platform.startswith("linux"):
        return "wayland" if os.environ.get("XDG_SESSION_TYPE") == "wayland" else "x11"
    return sys.platform


def input_status() -> str:
    """Whether input injection looks available, without prompting the user for anything."""
    kind = session_type()
    try:
        if kind == "wayland":
            from jeepney import DBusAddress, Properties
            from jeepney.io.blocking import open_dbus_connection

            portal = DBusAddress(
                "/org/freedesktop/portal/desktop",
                bus_name="org.freedesktop.portal.Desktop",
                interface="org.freedesktop.portal.RemoteDesktop",
            )
            conn = open_dbus_connection(bus="SESSION")
            try:
                reply = conn.send_and_get_reply(
                    Properties(portal).get("AvailableDeviceTypes"), timeout=3
                )
            finally:
                conn.close()
            devices = reply.body[0][1] if reply.body and isinstance(reply.body[0], tuple) else 0
            if not isinstance(devices, int) or devices & 3 != 3:
                return "unavailable: this desktop has no RemoteDesktop portal with keyboard and pointer"
            return "available through the desktop portal (asks for consent on first use)"
        if kind == "darwin":
            from superhero.platform.pynput_backend import accessibility_trusted

            trusted = accessibility_trusted()
            if trusted is None:
                return "unknown: could not query the Accessibility permission"
            return "available" if trusted else "blocked: grant the Accessibility permission"
        from superhero.platform.pynput_backend import PynputInput

        PynputInput()
        return "available"
    except Exception as exc:
        return f"unavailable: {exc}"


def create_input_backend(displays: Sequence[Display]) -> InputBackend:
    """Open the right adapter for this session. May prompt for OS permission.

    Raises :class:`InputUnavailable` when the platform denies injection.
    """
    if session_type() == "wayland":
        from superhero.platform.portal import PortalInput

        return PortalInput(displays)
    from superhero.platform.pynput_backend import PynputInput

    return PynputInput()
