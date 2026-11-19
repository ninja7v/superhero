"""Snapping and window descriptions, against a fake accessibility tree.

The real readers (AT-SPI, AX, UI Automation) need a desktop session; see docs/TESTING.md.
"""

import pytest

from superhero.platform import accessibility
from superhero.platform.accessibility import (
    Element,
    Window,
    describe_windows,
    snap_point,
    snapping_problem,
)
from superhero.platform.displays import Display

SCREEN = Display("main", 0, 0, 1920, 1080, primary=True)
HIDPI = Display("hidpi", 0, 0, 1280, 800, scale=2.0)


def tree(*elements):
    """element_at for a flat set of elements, recording each probe."""
    probes = []

    def element_at(x, y):
        probes.append((x, y))
        return next((e for e in elements if e.distance(x, y) == 0), None)

    element_at.probes = probes
    return element_at


def at(display, lx, ly):
    return display.normalize(lx, ly)


def test_a_click_near_a_button_lands_inside_it():
    button = Element("push button", 900, 500, 120, 30)
    snapped = snap_point(tree(button), SCREEN, *at(SCREEN, 960, 560), 48, platform="linux")
    lx, ly = SCREEN.logical_point(*snapped)
    assert button.distance(lx, ly) == 0
    assert lx == 960 and ly == pytest.approx(530 - 7.5, abs=1)  # straight up, just inside


def test_a_click_already_on_an_element_is_kept_with_one_probe():
    element_at = tree(Element("link", 900, 500, 120, 30))
    assert snap_point(element_at, SCREEN, *at(SCREEN, 950, 510), 48, platform="linux") is None
    assert len(element_at.probes) == 1


def test_nothing_within_reach_keeps_the_click():
    far = Element("push button", 100, 100, 50, 20)
    assert snap_point(tree(far), SCREEN, *at(SCREEN, 960, 540), 48, platform="linux") is None


def test_the_nearest_of_several_elements_wins():
    near = Element("push button", 1000, 530, 60, 20)  # 40 px to the right
    farther = Element("push button", 900, 590, 60, 20)  # 50 px below
    snapped = snap_point(tree(near, farther), SCREEN, *at(SCREEN, 960, 540), 60, platform="linux")
    assert near.distance(*SCREEN.logical_point(*snapped)) == 0


def test_the_search_stops_at_its_time_budget():
    now = [0.0]

    def clock():
        now[0] += 0.05
        return now[0]

    element_at = tree()
    snap_point(element_at, SCREEN, 0.5, 0.5, 48, budget_s=0.08, clock=clock, platform="linux")
    assert len(element_at.probes) < 17


def test_snapping_works_in_native_pixels_on_a_scaled_display():
    # The accessibility API reports native pixels: twice the logical ones here (X11).
    button = Element("push button", 1200, 800, 200, 60)  # logical 600,400 .. 700,430
    snapped = snap_point(tree(button), HIDPI, *at(HIDPI, 650, 450), 48, platform="linux")
    lx, ly = HIDPI.logical_point(*snapped)
    assert 600 <= lx <= 700 and 400 <= ly <= 430


def test_snapping_on_a_combined_display_probes_the_monitor_under_the_gaze():
    right = Display("right", 1920, 0, 1280, 1024)
    both = Display.combined([SCREEN, right])
    button = Element("push button", 2500, 480, 80, 30)
    snapped = snap_point(tree(button), both, *both.normalize(2540, 540), 48, platform="win32")
    assert button.distance(*both.logical_point(*snapped)) == 0


def test_windows_are_described_focused_first_and_trimmed():
    windows = [
        Window("nautilus", "docs"),
        Window("firefox", "Weather   in\nParis — Mozilla Firefox" + "x" * 200, focused=True),
        Window("calculator", "calculator"),
    ]
    lines = describe_windows(windows).splitlines()
    assert lines[0].startswith("- firefox: Weather in Paris") and lines[0].endswith("(focused)")
    assert len(lines[0]) < 110
    assert lines[2] == "- calculator"  # a title that only repeats the app name is dropped
    assert len(describe_windows(windows * 10, limit=15).splitlines()) == 15


def test_snapping_is_unavailable_on_wayland(monkeypatch):
    monkeypatch.setattr(accessibility, "session_type", lambda: "wayland")
    assert "Wayland" in snapping_problem("linux")
    monkeypatch.setattr(accessibility, "session_type", lambda: "x11")
    assert snapping_problem("linux") == ""
    assert snapping_problem("win32") == snapping_problem("darwin") == ""


class HungBus:
    """An accessibility bus where every application stops answering."""

    def __init__(self):
        self.waits = []

    def send_and_get_reply(self, _message, timeout):
        import time

        self.waits.append(timeout)
        time.sleep(timeout)
        raise TimeoutError


def hung_reader():
    import threading

    reader = accessibility.AtspiDesktop.__new__(accessibility.AtspiDesktop)  # no real bus
    reader._conn, reader._lock, reader.can_locate = HungBus(), threading.Lock(), True
    reader._active, reader._deadline = (float("-inf"), []), None
    return reader


def elapsed(call):
    import time

    start = time.monotonic()
    result = call()
    return result, time.monotonic() - start


def test_a_hung_application_cannot_hold_up_a_click():
    reader = hung_reader()
    # The focused-window list cannot be read: bounded by its own limit, and not cached.
    result, seconds = elapsed(lambda: reader.element_at(100, 100))
    assert result is None and seconds < accessibility._WINDOWS_BUDGET_S + 0.05
    assert reader._active[1] == [] and reader._active[0] == float("-inf")

    # The focused window is known but stops answering: bounded by the probe limit.
    import time

    reader._active = (time.monotonic() + 60, [(":1.42", "/org/a11y/atspi/accessible/1")])
    result, seconds = elapsed(lambda: reader.element_at(100, 100))
    assert result is None and seconds < accessibility._PROBE_BUDGET_S + 0.05

    # A whole snap, probing many points, stays within its budget plus one probe.
    result, seconds = elapsed(
        lambda: snap_point(reader.element_at, SCREEN, 0.5, 0.5, 48, platform="linux")
    )
    limit = accessibility.SNAP_BUDGET_S + 2 * accessibility._PROBE_BUDGET_S + 0.05
    assert result is None and seconds < limit


def test_a_click_does_not_wait_for_a_window_listing():
    import threading

    reader = hung_reader()
    listing = threading.Thread(target=lambda: reader._lock.acquire() and None)
    listing.start()
    listing.join()  # the lock is now held, as during a slow windows() call
    result, seconds = elapsed(lambda: reader.element_at(100, 100))
    assert result is None and seconds < accessibility._PROBE_BUDGET_S + 0.05


def test_controls_are_matched_by_name_ignoring_case_and_spacing():
    from superhero.platform.accessibility import match_control

    controls = [("Reply all", 1), ("Reply", 2), ("Search  mail", 3), ("Write", 4)]
    assert match_control(controls, "reply") == 2  # exact beats a longer name
    assert match_control(controls, "search mail") == 3
    assert match_control(controls, "Wri") == 4  # then a prefix
    assert match_control(controls, "mail") == 3  # then a part
    assert match_control(controls, "Delete") is None and match_control(controls, " ") is None


def test_the_desktop_is_described_windows_then_controls():
    from superhero.platform.accessibility import Control, describe_desktop

    text = describe_desktop(
        [Window("mail", "Inbox", focused=True)], [Control("push button", 'Say "hi"')]
    )
    assert text == (
        "Open windows, the focused one first:\n- mail: Inbox (focused)\n\n"
        "Controls in the focused window:\n- \"Say 'hi'\" (push button)"
    )
    assert describe_desktop([], []) == ""


class FakeTree(accessibility.AtspiDesktop):
    """An AT-SPI reader over an in-memory tree: path -> (role, name, states, children)."""

    SHOWN = 1 << 25 | 1 << 24  # showing, sensitive

    def __init__(self, nodes, actions=True):
        import threading

        self.nodes, self.actions, self.done = nodes, actions, []
        self._lock, self.can_locate, self._deadline = threading.Lock(), False, None
        self._active = (float("-inf"), [])

    def _toplevels(self):
        return [("mail", ("app", "/win"), 1 << 1 | self.SHOWN)]

    def _states(self, ref):
        return self.nodes[ref[1]][2]

    def _name(self, ref):
        return self.nodes[ref[1]][1]

    def _children(self, ref):
        return [("app", child) for child in self.nodes[ref[1]][3]]

    def _call(self, ref, interface, method, signature=None, body=()):
        if method == "GetRoleName":
            return (self.nodes[ref[1]][0],)
        if method == "DoAction" and not self.actions:
            raise accessibility.DesktopUnavailable("no Action interface")
        self.done.append((method, ref[1]))
        return (True,)


TREE = {
    "/win": ("frame", "Inbox", FakeTree.SHOWN, ["/bar", "/menu", "/search"]),
    "/bar": ("tool bar", "", FakeTree.SHOWN, ["/write", "/greyed"]),
    "/write": ("push button", "Write", FakeTree.SHOWN, []),
    "/greyed": ("push button", "Undo", 1 << 25, []),  # showing but not sensitive
    "/menu": ("menu", "File", 0, ["/quit"]),  # a closed menu: nothing inside is listed
    "/quit": ("menu item", "Quit", FakeTree.SHOWN, []),
    "/search": ("text", "Search", FakeTree.SHOWN, []),
}


def test_the_focused_windows_showing_enabled_controls_are_listed():
    from superhero.platform.accessibility import Control

    assert FakeTree(TREE).controls() == [Control("text", "Search"), Control("push button", "Write")]


def test_pressing_a_control_uses_its_action_or_else_focuses_it():
    reader = FakeTree(TREE)
    assert reader.press("write") and reader.done == [("DoAction", "/write")]
    fields = FakeTree(TREE, actions=False)
    assert fields.press("Search") and fields.done == [("GrabFocus", "/search")]
    assert not reader.press("Quit")  # inside a closed menu
    assert not reader.press("Undo")  # disabled


def test_a_hung_application_bounds_reading_and_pressing_controls():
    reader = hung_reader()
    result, seconds = elapsed(reader.controls)
    assert result == [] and seconds < accessibility._CONTROLS_BUDGET_S + 0.3
    result, seconds = elapsed(lambda: reader.press("Send"))
    assert result is False and seconds < accessibility._PRESS_BUDGET_S + 0.3
