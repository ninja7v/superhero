"""Wayland input injection and global shortcut through the XDG Desktop Portal.

Wayland gives clients no way to inject input or grab keys on their own; the
portal is the sanctioned route and it asks the user for consent. One
RemoteDesktop session (with a monitor stream, which absolute pointer motion
requires) carries pointer and keyboard. The grant is remembered through the
portal's restore token, so the consent dialog normally appears once.
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from jeepney import DBusAddress, MatchRule, MessageType, message_bus, new_method_call
from jeepney.io.blocking import DBusConnection, Proxy, open_dbus_connection

from superhero.config import data_dir
from superhero.platform.base import APP_ID, InputUnavailable, press_chord
from superhero.platform.displays import Display

log = logging.getLogger(__name__)

_BUS = "org.freedesktop.portal.Desktop"
_PATH = "/org/freedesktop/portal/desktop"
_BUTTONS = {"left": 0x110, "right": 0x111, "middle": 0x112}  # evdev BTN_* codes
_KEYSYMS = {
    "ctrl": 0xFFE3, "alt": 0xFFE9, "shift": 0xFFE1, "super": 0xFFEB, "cmd": 0xFFEB,
    "enter": 0xFF0D, "escape": 0xFF1B, "tab": 0xFF09, "space": 0x20, "backspace": 0xFF08,
    "delete": 0xFFFF, "up": 0xFF52, "down": 0xFF54, "left": 0xFF51, "right": 0xFF53,
    "home": 0xFF50, "end": 0xFF57, "pageup": 0xFF55, "pagedown": 0xFF56,
    **{f"f{i}": 0xFFBD + i for i in range(1, 13)},
}  # fmt: skip
_tokens = itertools.count(1)


def keysym(key: str) -> int:
    """X11 keysym for a canonical key name or a single character."""
    if key in _KEYSYMS:
        return _KEYSYMS[key]
    if len(key) != 1:
        raise ValueError(f"unknown key: {key!r}")
    if key == "\n":
        return _KEYSYMS["enter"]
    if key == "\t":
        return _KEYSYMS["tab"]
    code = ord(key)
    # Latin-1 maps directly; everything else uses the Unicode keysym range.
    return code if 0x20 <= code <= 0xFF else 0x01000000 + code


def _write_private(path: Path, text: str) -> None:
    """Write a secret readable by this user only.

    The restore token lets SUPERHERO regain pointer and keyboard control without the
    consent dialog, so other accounts on the machine must not be able to read it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)  # an older token file may have been created world-readable


def _address(interface: str) -> DBusAddress:
    return DBusAddress(_PATH, bus_name=_BUS, interface=f"org.freedesktop.portal.{interface}")


class _Portal:
    """A session-bus connection that can run the portal's request/response dance."""

    def __init__(self) -> None:
        try:
            self.conn: DBusConnection = open_dbus_connection(bus="SESSION")
        except Exception as exc:
            raise InputUnavailable(f"No D-Bus session bus: {exc}") from exc
        self._sender = self.conn.unique_name[1:].replace(".", "_")

    def call(self, interface: str, method: str, signature: str, body: tuple[Any, ...]) -> Any:
        reply = self.conn.send_and_get_reply(
            new_method_call(_address(interface), method, signature, body), timeout=10
        )
        if reply.header.message_type is MessageType.error:
            raise InputUnavailable(f"{interface}.{method} failed: {reply.body}")
        return reply.body

    def request(
        self,
        interface: str,
        method: str,
        signature: str,
        make_body: Callable[[dict[str, tuple[str, Any]]], tuple[Any, ...]],
        timeout: float = 30,
    ) -> dict[str, Any]:
        """Call a portal method that answers through a Request.Response signal."""
        token = f"superhero{next(_tokens)}"
        rule = MatchRule(
            type="signal",
            interface="org.freedesktop.portal.Request",
            member="Response",
            path=f"{_PATH}/request/{self._sender}/{token}",
        )
        Proxy(message_bus, self.conn).AddMatch(rule)
        with self.conn.filter(rule) as queue:
            self.call(interface, method, signature, make_body({"handle_token": ("s", token)}))
            try:
                response = self.conn.recv_until_filtered(queue, timeout=timeout)
            except TimeoutError as exc:
                raise InputUnavailable(f"No answer to the {interface} permission request.") from exc
        code, results = response.body
        if code != 0:
            reason = "was declined" if code == 1 else "ended unexpectedly"
            raise InputUnavailable(f"The {interface} permission request {reason}.")
        return {key: value for key, (_sig, value) in results.items()}

    def register(self) -> None:
        """Give this unsandboxed process an app identity, which some portals require.

        The portal resolves the id against installed ``.desktop`` files, so this
        fails until the desktop entry exists.
        """
        registry = DBusAddress(
            _PATH, bus_name=_BUS, interface="org.freedesktop.host.portal.Registry"
        )
        reply = self.conn.send_and_get_reply(
            new_method_call(registry, "Register", "sa{sv}", (APP_ID, {})), timeout=10
        )
        if reply.header.message_type is MessageType.error:
            raise InputUnavailable(
                "The global shortcut needs SUPERHERO's desktop entry, which has just been "
                "installed. Restart SUPERHERO; if this persists, log out and back in."
            )

    def create_session(self, interface: str) -> str:
        results = self.request(
            interface,
            "CreateSession",
            "a{sv}",
            lambda opts: ({**opts, "session_handle_token": ("s", f"superhero{next(_tokens)}")},),
        )
        return str(results["session_handle"])


class PortalInput:
    """Pointer and keyboard injection for Wayland sessions."""

    name = "portal"

    def __init__(self, displays: Sequence[Display], consent_timeout: float = 120) -> None:
        self._portal = _Portal()
        self._lock = threading.Lock()
        session = self._session = self._portal.create_session("RemoteDesktop")
        token_file = data_dir() / "portal_restore_token"
        select: dict[str, tuple[str, Any]] = {"types": ("u", 3), "persist_mode": ("u", 2)}
        if token_file.is_file():
            select["restore_token"] = ("s", token_file.read_text().strip())
        self._portal.request(
            "RemoteDesktop", "SelectDevices", "oa{sv}", lambda o: (session, {**o, **select})
        )
        self._portal.request(
            "ScreenCast",
            "SelectSources",
            "oa{sv}",
            lambda o: (session, {**o, "types": ("u", 1), "multiple": ("b", True)}),
        )
        started = self._portal.request(
            "RemoteDesktop", "Start", "osa{sv}", lambda o: (session, "", o), timeout=consent_timeout
        )
        if not started.get("devices", 0) & 2:
            raise InputUnavailable("Pointer control was not granted.")
        if token := started.get("restore_token"):
            _write_private(token_file, token)
        self._streams = self._match_streams(started.get("streams", []), displays)
        if not self._streams:
            raise InputUnavailable("No monitor was shared, so the pointer cannot be positioned.")

    @staticmethod
    def _match_streams(
        streams: Sequence[tuple[int, dict[str, tuple[str, Any]]]], displays: Sequence[Display]
    ) -> dict[str, tuple[int, int, int]]:
        """Map display name -> (stream node, width, height) by position and size.

        The portal reports the compositor's logical geometry, Qt the geometry XWayland
        shows it; with fractional scaling the two differ. Streams that match no display
        exactly are then paired with the unmatched displays in left-to-right, top-to-bottom
        order, but only when the counts agree, so the pairing is never a guess.
        """
        matched: dict[str, tuple[int, int, int]] = {}
        leftover: list[tuple[int, tuple[int, int] | None, tuple[int, int] | None]] = []
        for node, props in streams:
            position = props.get("position", ("", None))[1]
            size = props.get("size", ("", None))[1]
            for display in displays:
                same_place = position is None or tuple(position) == (display.x, display.y)
                same_size = size is None or tuple(size) == (display.width, display.height)
                if same_place and same_size and display.name not in matched:
                    width, height = size or (display.width, display.height)
                    matched[display.name] = (node, width, height)
                    break
            else:
                leftover.append((node, position, size))
        free = [d for d in displays if d.name not in matched]
        if leftover and len(leftover) == len(free):
            leftover.sort(key=lambda s: (tuple(s[1]) if s[1] is not None else (0, 0))[::-1])
            for (node, _position, size), display in zip(
                leftover, sorted(free, key=lambda d: (d.y, d.x)), strict=True
            ):
                width, height = size or (display.width, display.height)
                matched[display.name] = (node, width, height)
        return matched

    def _notify(self, method: str, signature: str, *args: Any) -> None:
        with self._lock:
            self._portal.call(
                "RemoteDesktop", method, "oa{sv}" + signature, (self._session, {}, *args)
            )

    def move(self, display: Display, nx: float, ny: float) -> None:
        stream = self._streams.get(display.name)
        if stream is None:
            raise InputUnavailable(f"Display {display.name} was not shared with SUPERHERO.")
        node, width, height = stream
        x = min(max(nx, 0.0), 1.0) * (width - 1)
        y = min(max(ny, 0.0), 1.0) * (height - 1)
        self._notify("NotifyPointerMotionAbsolute", "udd", node, x, y)

    def button(self, button: str, pressed: bool) -> None:
        self._notify("NotifyPointerButton", "iu", _BUTTONS[button], int(pressed))

    def scroll(self, lines: int) -> None:
        # Portal axis steps are positive downwards.
        self._notify("NotifyPointerAxisDiscrete", "ui", 0, -lines)

    def _key(self, key: str, pressed: bool) -> None:
        self._notify("NotifyKeyboardKeysym", "iu", keysym(key), int(pressed))

    def _press(self, key: str) -> None:
        self._key(key, True)

    def _release(self, key: str) -> None:
        self._key(key, False)

    def keys(self, combo: Sequence[str]) -> None:
        press_chord(self._press, self._release, combo)

    def type_text(self, text: str) -> None:
        for char in text:
            press_chord(self._press, self._release, [char])

    def close(self) -> None:
        with self._lock:
            session = DBusAddress(self._session, _BUS, "org.freedesktop.portal.Session")
            try:
                self._portal.conn.send(new_method_call(session, "Close"))
            finally:
                self._portal.conn.close()


class PortalHotkey:
    """A global shortcut on Wayland via the GlobalShortcuts portal."""

    def __init__(self, combo: str, callback: Callable[[], None]) -> None:
        self._portal = _Portal()
        self._portal.register()
        session = self._portal.create_session("GlobalShortcuts")
        trigger = "+".join(
            part.upper() if part in ("ctrl", "alt", "shift") else part.capitalize()
            for part in combo.split("+")
        )
        shortcut = (
            "emergency-stop",
            {
                "description": ("s", "Stop SUPERHERO control immediately"),
                "preferred_trigger": ("s", trigger),
            },
        )
        self._portal.request(
            "GlobalShortcuts",
            "BindShortcuts",
            "oa(sa{sv})sa{sv}",
            lambda o: (session, [shortcut], "", o),
            timeout=120,
        )
        self._callback = callback
        self._rule = MatchRule(
            type="signal",
            interface="org.freedesktop.portal.GlobalShortcuts",
            member="Activated",
            path=_PATH,
        )
        Proxy(message_bus, self._portal.conn).AddMatch(self._rule)
        threading.Thread(target=self._listen, name="superhero-hotkey", daemon=True).start()

    def _listen(self) -> None:
        conn = self._portal.conn
        try:
            with conn.filter(self._rule) as queue:
                while True:
                    conn.recv_until_filtered(queue)
                    self._callback()
        except Exception:
            log.debug("hotkey listener stopped", exc_info=True)

    def stop(self) -> None:
        self._portal.conn.close()
