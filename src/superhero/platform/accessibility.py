"""What is on the desktop: the open windows, their controls, and the element under a point.

AI commands are told which windows are open and which buttons, links and fields the
focused one has, and clicks can snap to the nearest actionable element, since gaze
from a webcam is a few degrees off. Each platform has its own accessibility API:
AT-SPI on Linux, the AX API on macOS, UI Automation on Windows. Everything here only
reads, except :meth:`Desktop.press`, which a confirmed AI plan uses to activate a
control by name.

Wayland does not tell clients where windows are, so there AT-SPI knows the windows
and their controls but not where those are on screen, and snapping is unavailable.
Pressing a control by name needs no position, so it works on Wayland too. Listing and
pressing controls is implemented for AT-SPI only so far.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from superhero.platform.base import session_type
from superhero.platform.displays import Display

# Clicks wait for snapping on the UI thread, so every lookup is bounded: one element_at
# call gives up after _PROBE_BUDGET_S, and snap_point stops probing after SNAP_BUDGET_S.
# A click is never held up by more than about their sum plus one probe.
_CALL_TIMEOUT_S = 0.25  # listing windows (for AI requests, off the UI thread)
_PROBE_BUDGET_S = 0.06
_WINDOWS_BUDGET_S = 0.15  # re-reading which window has focus, at most twice a second
SNAP_BUDGET_S = 0.08
_CONTROLS_BUDGET_S = 0.6  # reading the focused window's controls for an AI request
_PRESS_BUDGET_S = 1.0
_MAX_CONTROLS = 60
_MAX_NODES = 1500


class DesktopUnavailable(RuntimeError):
    """The accessibility API is missing, refused, or not permitted."""


@dataclass(frozen=True)
class Window:
    app: str
    title: str
    focused: bool = False


@dataclass(frozen=True)
class Element:
    """An actionable element, in the platform's native screen coordinates."""

    role: str
    x: float
    y: float
    width: float
    height: float

    def distance(self, px: float, py: float) -> float:
        dx = max(self.x - px, 0.0, px - (self.x + self.width))
        dy = max(self.y - py, 0.0, py - (self.y + self.height))
        return math.hypot(dx, dy)

    def inner_point(self, px: float, py: float) -> tuple[float, float]:
        """The point inside the element nearest to (px, py), kept off its edges."""
        ix, iy = min(self.width / 4, 8.0), min(self.height / 4, 8.0)
        return (
            min(max(px, self.x + ix), self.x + self.width - ix),
            min(max(py, self.y + iy), self.y + self.height - iy),
        )


@dataclass(frozen=True)
class Control:
    """A named, actionable control of the focused window."""

    role: str
    name: str


class Desktop(Protocol):
    can_locate: bool  # whether element_at can work at all in this session

    def windows(self) -> list[Window]:
        """Open, visible windows; the focused one is marked."""

    def controls(self) -> list[Control]:
        """Named buttons, links, fields and the like in the focused window, outermost first."""

    def press(self, name: str) -> bool:
        """Activate the focused window's control called ``name``; False if none was found."""

    def element_at(self, x: float, y: float) -> Element | None:
        """The actionable element at a native screen point, or None."""

    def close(self) -> None: ...


def snapping_problem(platform: str = sys.platform) -> str:
    """Why clicks cannot snap in this session, or an empty string. Opens nothing."""
    if platform.startswith("linux"):
        if session_type() == "wayland":
            return "Wayland does not tell applications where windows are, so snapping cannot work here."
        return ""
    if platform in ("win32", "darwin"):
        return ""
    return f"Not available on {platform}."


def describe_windows(windows: list[Window], limit: int = 15, title_chars: int = 80) -> str:
    """One line per window, the focused one first, for the AI's prompt."""
    ordered = sorted(windows, key=lambda w: not w.focused)[:limit]
    lines = []
    for window in ordered:
        title = " ".join(window.title.split())[:title_chars]
        line = f"- {window.app}" + (f": {title}" if title and title != window.app else "")
        lines.append(line + (" (focused)" if window.focused else ""))
    return "\n".join(lines)


def describe_controls(controls: list[Control], name_chars: int = 60) -> str:
    """One line per control, for the AI's prompt: ``- "Send" (push button)``."""
    lines = []
    for control in controls:
        name = " ".join(control.name.split())[:name_chars].replace('"', "'")
        lines.append(f'- "{name}" ({control.role})')
    return "\n".join(lines)


def describe_desktop(windows: list[Window], controls: list[Control]) -> str:
    """What the AI is told about the desktop: the open windows, then the focused one's
    controls. Empty when nothing is known."""
    parts = []
    if windows:
        parts.append("Open windows, the focused one first:\n" + describe_windows(windows))
    if controls:
        parts.append("Controls in the focused window:\n" + describe_controls(controls))
    return "\n\n".join(parts)


def match_control[T](controls: list[tuple[str, T]], name: str) -> T | None:
    """The control called ``name``: an exact match first, then one that starts with it,
    then one that contains it. Case and spacing are ignored."""
    wanted = " ".join(name.split()).casefold()
    if not wanted:
        return None
    named = [(" ".join(n.split()).casefold(), item) for n, item in controls]
    for test in (str.__eq__, str.startswith, str.__contains__):
        for candidate, item in named:
            if test(candidate, wanted):
                return item
    return None


def snap_point(
    element_at: Callable[[float, float], Element | None],
    display: Display,
    nx: float,
    ny: float,
    radius: float,
    *,
    budget_s: float = SNAP_BUDGET_S,
    platform: str = sys.platform,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[float, float] | None:
    """Where a click at (nx, ny) on ``display`` should land instead, or None to keep it.

    Probes the point, then two rings around it (``radius`` is in logical pixels), and
    picks the nearest actionable element within the radius. A point already on an
    element is kept. The search stops at ``budget_s``; what was found by then is used.
    """
    part, px, py = display.locate(nx, ny)
    cx, cy = part.native_point(px, py, platform)
    reach = radius * (1.0 if platform == "darwin" else part.scale)
    deadline = clock() + budget_s
    hit = element_at(cx, cy)
    if hit is not None and hit.distance(cx, cy) == 0:
        return None
    found = [hit] if hit is not None else []
    for fraction in (0.5, 1.0):
        for step in range(8):
            if clock() > deadline:
                break
            angle = step * math.pi / 4
            element = element_at(
                cx + fraction * reach * math.cos(angle), cy + fraction * reach * math.sin(angle)
            )
            if element is not None and element not in found:
                found.append(element)
    near = [e for e in found if e.distance(cx, cy) <= reach]
    if not near:
        return None
    target = min(near, key=lambda e: e.distance(cx, cy))
    lx, ly = part.from_native(*target.inner_point(cx, cy), platform)
    return display.normalize(lx, ly)


def open_desktop(platform: str = sys.platform) -> Desktop:
    """The accessibility reader for this platform. Raises :class:`DesktopUnavailable`."""
    try:
        if platform == "darwin":
            return MacDesktop()
        if platform == "win32":
            return WindowsDesktop()
        if platform.startswith("linux"):
            return AtspiDesktop(can_locate=session_type() != "wayland")
    except DesktopUnavailable:
        raise
    except Exception as exc:
        raise DesktopUnavailable(f"{type(exc).__name__}: {exc}") from exc
    raise DesktopUnavailable(f"No accessibility support on {platform}.")


# -- Linux: AT-SPI over D-Bus ---------------------------------------------------------------

_ATSPI_ACTIONABLE = {
    "push button", "toggle button", "button", "check box", "radio button", "switch", "link",
    "menu item", "check menu item", "radio menu item", "menu", "combo box", "entry",
    "password text", "text", "page tab", "list item", "table cell", "tree item", "slider",
    "spin button", "icon",
}  # fmt: skip
# Not applications: the shell's stage, and the window frames mutter draws for X11 windows.
_ATSPI_NOT_APPS = {"gnome-shell", "mutter-x11-frames"}
_ACTIVE, _SENSITIVE, _SHOWING = 1, 24, 25  # AtspiStateType bits
_ATSPI_NULL = "/org/a11y/atspi/null"
_ACCESSIBLE = "org.a11y.atspi.Accessible"
_COMPONENT = "org.a11y.atspi.Component"
_SCREEN = 0  # ATSPI_COORD_TYPE_SCREEN

Ref = tuple[str, str]  # (bus name, object path) of an accessible


class AtspiDesktop:
    """Reads the AT-SPI accessibility bus, which GTK, Qt and most Linux toolkits serve."""

    def __init__(self, can_locate: bool = True) -> None:
        import jeepney.io.blocking as blocking  # looked up here so tests can refuse D-Bus
        from jeepney import DBusAddress, new_method_call

        session = blocking.open_dbus_connection(bus="SESSION")
        try:
            bus = DBusAddress("/org/a11y/bus", "org.a11y.Bus", "org.a11y.Bus")
            reply = session.send_and_get_reply(new_method_call(bus, "GetAddress"), timeout=2)
        finally:
            session.close()
        if not reply.body or not isinstance(reply.body[0], str):
            raise DesktopUnavailable("The accessibility bus is not running.")
        self._conn = blocking.open_dbus_connection(bus=reply.body[0])
        self._lock = threading.Lock()
        self.can_locate = can_locate
        self._active: tuple[float, list[Ref]] = (float("-inf"), [])
        self._deadline: float | None = None  # set while element_at runs

    def _timeout(self) -> float:
        if self._deadline is None:
            return _CALL_TIMEOUT_S
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("out of time for this lookup")
        return min(left, _CALL_TIMEOUT_S)

    def _call(
        self, ref: Ref, interface: str, method: str, signature: str | None = None,
        body: tuple[Any, ...] = (),
    ) -> Any:  # fmt: skip
        from jeepney import DBusAddress, MessageType, new_method_call

        message = new_method_call(DBusAddress(ref[1], ref[0], interface), method, signature, body)
        reply = self._conn.send_and_get_reply(message, timeout=self._timeout())
        if reply.header.message_type is MessageType.error:
            raise DesktopUnavailable(f"{method}: {reply.body}")
        return reply.body

    def _name(self, ref: Ref) -> str:
        from jeepney import DBusAddress, MessageType, Properties

        message = Properties(DBusAddress(ref[1], ref[0], _ACCESSIBLE)).get("Name")
        reply = self._conn.send_and_get_reply(message, timeout=self._timeout())
        if reply.header.message_type is MessageType.error:
            return ""
        return str(reply.body[0][1])

    def _children(self, ref: Ref) -> list[Ref]:
        return [
            (str(bus), str(path)) for bus, path in self._call(ref, _ACCESSIBLE, "GetChildren")[0]
        ]

    def _states(self, ref: Ref) -> int:
        low, high = self._call(ref, _ACCESSIBLE, "GetState")[0]
        return low | high << 32

    def _toplevels(self) -> list[tuple[str, Ref, int]]:
        """(application name, window, state bits) for every showing top-level window.

        Toolkits disagree on the role (GTK says frame or window, Qt says filler), so every
        showing child of an application counts, except tooltips.
        """
        root = ("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root")
        result = []
        for app in self._children(root):
            try:
                app_name = self._name(app)
                if app_name in _ATSPI_NOT_APPS:
                    continue
                for window in self._children(app):
                    role = self._call(window, _ACCESSIBLE, "GetRoleName")[0]
                    states = self._states(window)
                    if role != "tool tip" and states >> _SHOWING & 1:
                        result.append((app_name, window, states))
            except (DesktopUnavailable, TimeoutError, OSError, ValueError):
                continue  # an application that does not answer is left out
        return result

    def windows(self) -> list[Window]:
        with self._lock:
            found = []
            for app, window, states in self._toplevels():
                try:
                    title = self._name(window)
                except (TimeoutError, OSError):
                    continue
                if title and not (app == "gjs" and title.startswith("Desktop Icons")):
                    # (GNOME's desktop icons are drawn by a window of their own)
                    found.append(Window(app, title, bool(states >> _ACTIVE & 1)))
            return found

    def _active_windows(self) -> list[Ref]:
        """The focused window, re-read at most twice a second (a click probes many points).

        A walk cut short by its time limit is not kept, so the next click tries again.
        """
        read_at, windows = self._active
        if time.monotonic() - read_at > 0.5:
            self._deadline = time.monotonic() + _WINDOWS_BUDGET_S
            windows = [w for _app, w, states in self._toplevels() if states >> _ACTIVE & 1]
            if time.monotonic() < self._deadline:
                self._active = (time.monotonic(), windows)
        return windows

    def element_at(self, x: float, y: float) -> Element | None:
        """Descend the focused window to the deepest element at (x, y), keeping the last
        actionable one on the way: the label inside a button resolves to the button."""
        if not self.can_locate:
            return None
        # Listing windows for an AI request may hold the lock; a click does not wait for it.
        if not self._lock.acquire(timeout=_PROBE_BUDGET_S):
            return None
        try:
            try:
                active = self._active_windows()
                self._deadline = time.monotonic() + _PROBE_BUDGET_S
                for node in active:
                    best: tuple[str, Ref] | None = None
                    for _depth in range(40):
                        reply = self._call(
                            node, _COMPONENT, "GetAccessibleAtPoint", "iiu",
                            (round(x), round(y), _SCREEN),
                        )  # fmt: skip
                        child = (str(reply[0][0]), str(reply[0][1]))
                        if child[1] == _ATSPI_NULL or child == node:
                            break
                        role = self._call(child, _ACCESSIBLE, "GetRoleName")[0]
                        if role in _ATSPI_ACTIONABLE:
                            states = self._states(child)
                            if states >> _SHOWING & 1 and states >> _SENSITIVE & 1:
                                best = (role, child)
                        node = child
                    if best is not None:
                        extents = self._call(best[1], _COMPONENT, "GetExtents", "u", (_SCREEN,))
                        ex, ey, ew, eh = extents[0]
                        return Element(best[0], ex, ey, ew, eh) if ew > 0 and eh > 0 else None
            except (DesktopUnavailable, TimeoutError, OSError, ValueError):
                return None
            return None
        finally:
            self._deadline = None
            self._lock.release()

    def _walk_controls(self, limit: int) -> list[tuple[str, str, Ref]]:
        """(role, name, ref) of the showing, enabled, named controls of the focused window,
        breadth first, until ``limit`` are found or the deadline passes."""
        found: list[tuple[str, str, Ref]] = []
        visited = 0
        try:
            queue = [w for _app, w, states in self._toplevels() if states >> _ACTIVE & 1]
            while queue and len(found) < limit and visited < _MAX_NODES:
                node = queue.pop(0)
                visited += 1
                try:
                    states = self._states(node)
                    if not states >> _SHOWING & 1:
                        continue  # a hidden subtree: closed menus, other tabs
                    role = self._call(node, _ACCESSIBLE, "GetRoleName")[0]
                    if role in _ATSPI_ACTIONABLE and states >> _SENSITIVE & 1:
                        name = self._name(node).strip()
                        if name:
                            found.append((str(role), name, node))
                    queue.extend(self._children(node)[:200])
                except (DesktopUnavailable, OSError, ValueError):
                    continue  # one element that does not answer
        except TimeoutError:
            pass  # out of time: what was found so far is used
        return found

    def controls(self) -> list[Control]:
        with self._lock:
            self._deadline = time.monotonic() + _CONTROLS_BUDGET_S
            try:
                return [
                    Control(role, name) for role, name, _ref in self._walk_controls(_MAX_CONTROLS)
                ]
            finally:
                self._deadline = None

    def press(self, name: str) -> bool:
        with self._lock:
            self._deadline = time.monotonic() + _PRESS_BUDGET_S
            try:
                found = self._walk_controls(_MAX_NODES)
                ref = match_control([(n, r) for _role, n, r in found], name)
                if ref is None:
                    return False
                self._deadline = time.monotonic() + _CALL_TIMEOUT_S * 2
                try:  # buttons, links and menu items have an action ...
                    if self._call(ref, "org.a11y.atspi.Action", "DoAction", "i", (0,))[0]:
                        return True
                except DesktopUnavailable:
                    pass
                # ... and a text field without one can still take the keyboard focus.
                return bool(self._call(ref, _COMPONENT, "GrabFocus")[0])
            except (DesktopUnavailable, TimeoutError, OSError, ValueError):
                return False
            finally:
                self._deadline = None

    def close(self) -> None:
        self._conn.close()


# -- macOS: the AX API ------------------------------------------------------------------------

_AX_ACTIONABLE = {
    "AXButton", "AXCheckBox", "AXRadioButton", "AXMenuItem", "AXMenuBarItem", "AXMenuButton",
    "AXPopUpButton", "AXComboBox", "AXTextField", "AXTextArea", "AXLink", "AXCell", "AXRow",
    "AXSlider", "AXIncrementor", "AXDisclosureTriangle", "AXTab",
}  # fmt: skip


class MacDesktop:
    """The macOS accessibility API (needs the same Accessibility permission as input)."""

    can_locate = True

    def __init__(self) -> None:
        import ApplicationServices as ax  # pyobjc, installed with pynput on macOS

        if not ax.AXIsProcessTrusted():
            raise DesktopUnavailable("macOS has not granted the Accessibility permission.")
        self._ax = ax
        self._system = ax.AXUIElementCreateSystemWide()
        # Per message; an application that does not answer costs at most this per lookup step.
        ax.AXUIElementSetMessagingTimeout(self._system, 0.03)

    def _attribute(self, element: Any, name: str) -> Any:
        error, value = self._ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if error == 0 else None

    def element_at(self, x: float, y: float) -> Element | None:
        ax = self._ax
        deadline = time.monotonic() + _PROBE_BUDGET_S
        error, element = ax.AXUIElementCopyElementAtPosition(self._system, x, y, None)
        if error:
            return None
        for _level in range(8):  # the text inside a button is often what is hit
            if element is None or time.monotonic() > deadline:
                return None
            role = self._attribute(element, "AXRole")
            if role in _AX_ACTIONABLE and self._attribute(element, "AXEnabled") is not False:
                position = self._attribute(element, "AXPosition")
                size = self._attribute(element, "AXSize")
                if position is None or size is None:
                    return None
                _ok, point = ax.AXValueGetValue(position, ax.kAXValueCGPointType, None)
                _ok, extent = ax.AXValueGetValue(size, ax.kAXValueCGSizeType, None)
                return Element(str(role), point.x, point.y, extent.width, extent.height)
            element = self._attribute(element, "AXParent")
        return None

    def windows(self) -> list[Window]:
        import AppKit
        import Quartz

        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        front_name = str(front.localizedName()) if front is not None else ""
        options = (
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        )
        result, focus_given = [], False
        for info in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
            if info.get("kCGWindowLayer", 0) != 0:
                continue  # menus, the dock, overlays
            app = str(info.get("kCGWindowOwnerName", ""))
            # The list runs front to back, so the frontmost app's first window has focus.
            # Titles need the Screen Recording permission; without it only app names come.
            focused = app == front_name and not focus_given
            focus_given = focus_given or focused
            result.append(Window(app, str(info.get("kCGWindowName", "") or ""), focused))
        return result

    def controls(self) -> list[Control]:
        return []  # not implemented on macOS yet

    def press(self, name: str) -> bool:
        return False

    def close(self) -> None:
        pass


# -- Windows: the window list from user32, elements from UI Automation -----------------------

_UIA_ACTIONABLE = {
    50000: "button", 50002: "check box", 50003: "combo box", 50004: "edit", 50005: "link",
    50007: "list item", 50011: "menu item", 50013: "radio button", 50015: "slider",
    50016: "spinner", 50019: "tab item", 50024: "tree item", 50029: "data item",
    50031: "split button",
}  # fmt: skip


class WindowsDesktop:
    can_locate = True

    def __init__(self) -> None:
        self._api = _win32_api()
        self._local = threading.local()  # COM objects belong to the thread that made them
        self._automation()  # fail now rather than at the first click

    def _automation(self) -> Any:
        uia = getattr(self._local, "uia", None)
        if uia is None:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()
            module = comtypes.client.GetModule("UIAutomationCore.dll")
            uia = comtypes.client.CreateObject(module.CUIAutomation, interface=module.IUIAutomation)
            self._local.uia, self._local.walker = uia, uia.ControlViewWalker
        return uia

    def element_at(self, x: float, y: float) -> Element | None:
        from ctypes.wintypes import POINT

        deadline = time.monotonic() + _PROBE_BUDGET_S
        try:
            element = self._automation().ElementFromPoint(POINT(round(x), round(y)))
            for _level in range(8):  # the text inside a button is often what is hit
                if element is None or time.monotonic() > deadline:
                    return None
                role = _UIA_ACTIONABLE.get(element.CurrentControlType)
                if role and element.CurrentIsEnabled and not element.CurrentIsOffscreen:
                    r = element.CurrentBoundingRectangle
                    return Element(role, r.left, r.top, r.right - r.left, r.bottom - r.top)
                element = self._local.walker.GetParentElement(element)
        except Exception:  # COMError and friends: an application that does not answer
            return None
        return None

    def windows(self) -> list[Window]:
        import ctypes
        from ctypes import wintypes
        from pathlib import Path

        user32, kernel32, dwmapi = self._api
        foreground = user32.GetForegroundWindow()
        found: list[Window] = []

        def app_name(hwnd: int) -> str:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
            if not handle:
                return ""
            try:
                size = wintypes.DWORD(260)
                path = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    return Path(path.value).stem
                return ""
            finally:
                kernel32.CloseHandle(handle)

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[attr-defined]
        def visit(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindowLongW(hwnd, -20) & 0x80:
                return True  # hidden, or a tool window (WS_EX_TOOLWINDOW)
            cloaked = wintypes.DWORD()
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
            length = user32.GetWindowTextLengthW(hwnd)
            if cloaked.value or not length:
                return True  # suspended store apps, and untitled helper windows
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            found.append(Window(app_name(hwnd), title.value, hwnd == foreground))
            return True

        user32.EnumWindows(visit, 0)
        return found

    def controls(self) -> list[Control]:
        return []  # not implemented on Windows yet

    def press(self, name: str) -> bool:
        return False

    def close(self) -> None:
        pass


def _win32_api() -> tuple[Any, Any, Any]:
    """user32, kernel32 and dwmapi with the signatures this module calls.

    Private instances, so these declarations cannot clash with another library's (pynput
    shares ``ctypes.windll``). Handles are pointer-sized: ctypes would otherwise pass them
    as 32-bit ints, which overflows on 64-bit Windows.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    dwmapi = ctypes.WinDLL("dwmapi")  # type: ignore[attr-defined]
    hwnd, dword = wintypes.HWND, wintypes.DWORD
    signatures = [
        (user32.GetForegroundWindow, [], hwnd),
        (user32.IsWindowVisible, [hwnd], wintypes.BOOL),
        (user32.GetWindowLongW, [hwnd, ctypes.c_int], wintypes.LONG),
        (user32.GetWindowTextLengthW, [hwnd], ctypes.c_int),
        (user32.GetWindowTextW, [hwnd, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32.GetWindowThreadProcessId, [hwnd, ctypes.POINTER(dword)], dword),
        (user32.EnumWindows, [ctypes.c_void_p, wintypes.LPARAM], wintypes.BOOL),
        (kernel32.OpenProcess, [dword, wintypes.BOOL, dword], wintypes.HANDLE),
        (kernel32.QueryFullProcessImageNameW,
         [wintypes.HANDLE, dword, wintypes.LPWSTR, ctypes.POINTER(dword)], wintypes.BOOL),
        (kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
        (dwmapi.DwmGetWindowAttribute, [hwnd, dword, ctypes.c_void_p, dword], ctypes.c_long),
    ]  # fmt: skip
    for function, argtypes, restype in signatures:
        function.argtypes, function.restype = argtypes, restype
    return user32, kernel32, dwmapi
