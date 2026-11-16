"""Checks that run against the real operating system's input layer where it is safe to."""

import os
import sys

import pytest

from superhero.platform.base import input_status, session_type
from superhero.platform.displays import Display
from superhero.platform.hotkey import pynput_combo

HEADLESS_LINUX = sys.platform.startswith("linux") and not os.environ.get("DISPLAY")
ON_WAYLAND = session_type() == "wayland"


def test_hotkey_combo_translation():
    assert pynput_combo("ctrl+alt+escape") == "<ctrl>+<alt>+<esc>"
    assert pynput_combo("Ctrl+Shift+P") == "<ctrl>+<shift>+p"


def test_session_type_matches_the_platform():
    kind = session_type()
    if sys.platform == "win32":
        assert kind == "win32"
    elif sys.platform == "darwin":
        assert kind == "darwin"
    else:
        assert kind in ("x11", "wayland")


def test_input_status_never_raises_and_never_prompts():
    status = input_status()
    assert status.split(":")[0].split(" ")[0] in ("available", "unavailable", "blocked", "unknown")


def trusted_for_injection():
    from superhero.platform.pynput_backend import accessibility_trusted

    return accessibility_trusted() is not False


@pytest.mark.skipif(HEADLESS_LINUX or ON_WAYLAND, reason="needs an X11, Windows or macOS session")
def test_native_backend_knows_every_canonical_key():
    if not trusted_for_injection():
        pytest.skip("macOS Accessibility permission not granted to this process")
    from superhero.platform.base import KEY_NAMES
    from superhero.platform.pynput_backend import PynputInput

    backend = PynputInput()
    assert set(KEY_NAMES) <= set(backend._keys)
    assert set(backend._buttons) == {"left", "right", "middle"}


@pytest.mark.real_input
@pytest.mark.skipif(sys.platform != "win32", reason="checks SendInput's coordinate mapping")
def test_windows_pointer_really_moves():
    from pynput import mouse

    from superhero.platform.pynput_backend import PynputInput

    backend = PynputInput()
    display = Display("ci", 0, 0, 800, 600, primary=True)
    for nx, ny in ((0.25, 0.25), (0.5, 0.75)):
        backend.move(display, nx, ny)
        assert mouse.Controller().position == pytest.approx(display.native_point(nx, ny), abs=2)
