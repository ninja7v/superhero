"""Platform helpers that need no real input device: chords, desktop entries, private files."""

import os
import stat
import sys

import pytest

from superhero.platform.base import press_chord


class Keyboard:
    def __init__(self, fail_press=None, fail_release=None):
        self.down, self.log = set(), []
        self._fail_press, self._fail_release = fail_press, fail_release

    def press(self, key):
        if key == self._fail_press:
            raise ValueError(f"unknown key {key}")
        self.down.add(key)
        self.log.append(("press", key))

    def release(self, key):
        self.log.append(("release", key))
        if key == self._fail_release:
            raise RuntimeError("release failed")
        self.down.discard(key)


def test_chord_presses_in_order_and_releases_in_reverse():
    kb = Keyboard()
    press_chord(kb.press, kb.release, ["ctrl", "alt", "t"])
    assert kb.log == [
        ("press", "ctrl"), ("press", "alt"), ("press", "t"),
        ("release", "t"), ("release", "alt"), ("release", "ctrl"),
    ]  # fmt: skip
    assert kb.down == set()


def test_a_failed_press_never_leaves_modifiers_held():
    kb = Keyboard(fail_press="tt")
    with pytest.raises(ValueError):
        press_chord(kb.press, kb.release, ["ctrl", "alt", "tt"])
    assert kb.down == set()
    assert kb.log[-2:] == [("release", "alt"), ("release", "ctrl")]


def test_a_failed_release_still_releases_the_rest():
    kb = Keyboard(fail_release="alt")
    with pytest.raises(RuntimeError):
        press_chord(kb.press, kb.release, ["ctrl", "alt", "t"])
    assert "ctrl" not in kb.down and ("release", "ctrl") in kb.log


def test_portal_typing_releases_each_key_even_when_the_session_fails():
    pytest.importorskip("jeepney")
    from superhero.platform.portal import PortalInput

    portal = object.__new__(PortalInput)  # no D-Bus: only the key logic is exercised
    sent = []

    def key(name, pressed):
        if name == "x" and pressed:
            raise RuntimeError("session closed")
        sent.append((name, pressed))

    portal._key = key
    portal.keys(["ctrl", "shift", "t"])
    assert sent == [
        ("ctrl", True), ("shift", True), ("t", True), ("t", False), ("shift", False), ("ctrl", False),
    ]  # fmt: skip
    sent.clear()
    with pytest.raises(RuntimeError):
        portal.type_text("ax")
    assert sent == [("a", True), ("a", False)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_restore_token_is_private(tmp_path):
    pytest.importorskip("jeepney")
    from superhero.platform.portal import _write_private

    token = tmp_path / "data" / "portal_restore_token"
    token.parent.mkdir()
    token.write_text("old")
    os.chmod(token, 0o644)  # as written by earlier versions
    _write_private(token, "secret")
    assert token.read_text() == "secret"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600


def test_desktop_entry_exec_is_quoted_per_the_spec():
    from superhero.app import desktop_exec

    assert desktop_exec(["/usr/bin/superhero"]) == "/usr/bin/superhero"
    assert (
        desktop_exec(["/home/a b/python", "-m", "superhero"]) == '"/home/a b/python" -m superhero'
    )
    # `$` is escaped for the quoting rule, then every backslash doubled for the string rule;
    # `%` is doubled so it is not a field code.
    assert desktop_exec(["/opt/x$y/50%"]) == '"/opt/x\\\\$y/50%%"'


def test_packaged_build_launches_its_own_executable(monkeypatch):
    from superhero import app

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/superhero/superhero")
    assert app.launch_command() == ["/opt/superhero/superhero"]
    monkeypatch.delattr(sys, "frozen")
    monkeypatch.setattr(app.shutil, "which", lambda _name: None)
    assert app.launch_command() == ["/opt/superhero/superhero", "-m", "superhero"]


def test_install_desktop_writes_the_entry_the_portal_looks_for(tmp_path, monkeypatch):
    from superhero.app import install_desktop_entry
    from superhero.platform.base import APP_ID, desktop_entry_path

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    path = install_desktop_entry()
    assert path == desktop_entry_path() == tmp_path / "applications" / f"{APP_ID}.desktop"
    text = path.read_text()
    assert text.startswith("[Desktop Entry]\n") and "\nExec=" in text
