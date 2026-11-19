"""The whole application, offscreen, with recorded landmarks instead of a camera and model."""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from superhero.perception import pipeline as pipeline_module
from tests.test_pipeline import FakeCamera, ReplayEstimator


def pump(app, until, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if until():
            return True
        time.sleep(0.01)
    return False


def test_app_starts_tracks_and_quits(monkeypatch):
    from superhero.app import Controller

    monkeypatch.setattr(pipeline_module, "FaceLandmarkEstimator", ReplayEstimator)
    monkeypatch.setattr(pipeline_module, "download_face_landmarker", lambda: Path("unused"))
    ReplayEstimator.jaw_open, ReplayEstimator.face_visible = 0.0, True
    app = QApplication.instance() or QApplication([])
    controller = Controller("test", camera=FakeCamera(), global_hotkey=False)
    try:
        controller.start()
        assert pump(app, lambda: controller.engine.pointer is not None), "no gaze reached the UI"
        assert controller.overlay.isVisible() and controller.panel.isVisible()

        # Every settings page builds and the status card fills in.
        pages = controller.panel._nav.count()
        assert pages == 9
        for row in range(pages):
            controller.panel._nav.setCurrentRow(row)
            app.processEvents()
        controller._refresh_status()
        status = controller.panel.status_text()
        assert "Camera: Running" in status and "Mouth opening:" in status
        assert "Calibrate first" in status  # the hero explains why control is off

        # The laser preview, the colour swatches and the voice-command empty state exist.
        assert controller.panel.pointer_preview is not None
        assert controller.panel._commands_empty.isVisibleTo(
            controller.panel._stack.widget(6).widget()
        )

        # Control cannot be switched on before a calibration exists.
        controller.set_armed(True)
        assert not controller.engine.armed

        # With a tray, the panel hides instead of closing, yet a real quit is not blocked by it.
        controller.tray_available = True
        controller.panel.close()
        assert not controller.panel.isVisible()
        controller.panel.show()
        controller.quitting = True
        assert controller.panel.close()
    finally:
        controller.shutdown()


def test_doctor_reports_core_components():
    from superhero.doctor import report

    rows = dict(report())
    for label in ("Displays", "Overlay", "Gaze engine", "Speech"):
        assert not rows[label].startswith("FAILED"), rows[label]
    assert rows["Session"] in ("x11", "wayland", "win32", "darwin")


class FakeInput:
    name = "fake"

    def __init__(self):
        self.calls, self.closed = [], False

    def move(self, display, nx, ny):
        self.calls.append(("move", nx, ny))

    def button(self, button, pressed):
        self.calls.append(("button", button, pressed))

    def scroll(self, lines):
        pass

    def keys(self, combo):
        pass

    def type_text(self, text):
        pass

    def close(self):
        self.closed = True


def make_controller(monkeypatch):
    from superhero.app import Controller

    monkeypatch.setattr(pipeline_module, "FaceLandmarkEstimator", ReplayEstimator)
    monkeypatch.setattr(pipeline_module, "download_face_landmarker", lambda: Path("unused"))
    ReplayEstimator.jaw_open, ReplayEstimator.face_visible = 0.0, True
    app = QApplication.instance() or QApplication([])
    return app, Controller("test", camera=FakeCamera(), global_hotkey=False)


def test_control_is_switched_off_on_an_uncalibrated_pipeline(monkeypatch):
    """Switching camera or display restarts the pipeline; its pair may have no calibration."""
    app, controller = make_controller(monkeypatch)
    try:
        controller.start()
        assert pump(app, lambda: controller.engine.pointer is not None)
        controller.engine.input = FakeInput()
        controller.engine.set_armed(True)  # as if armed while the old pipeline was calibrated
        assert pump(app, lambda: not controller.engine.armed)
        assert "calibrate" in controller._flash
    finally:
        controller.shutdown()


def test_events_from_a_replaced_pipeline_are_ignored(monkeypatch):
    from fovea.events import GazePoint

    _app, controller = make_controller(monkeypatch)
    try:
        controller._on_events([GazePoint(0.9, 0.9, 1.0, 0)], None, controller._generation - 1)
        assert controller.engine.pointer is None
        controller._on_events([GazePoint(0.9, 0.9, 1.0, 0)], None, controller._generation)
        assert controller.engine.pointer == (0.9, 0.9)
    finally:
        controller.shutdown()


def test_one_permission_request_at_a_time(monkeypatch):
    import threading
    from types import SimpleNamespace

    from superhero import app as app_module

    release, requests = threading.Event(), []

    def slow_permission(_displays):
        requests.append(1)
        release.wait(5)
        return FakeInput()

    monkeypatch.setattr(app_module, "create_input_backend", slow_permission)
    app, controller = make_controller(monkeypatch)
    try:
        controller.pipeline = SimpleNamespace(calibrated=True, stop=lambda: None)
        for _ in range(3):
            controller.set_armed(True)
        release.set()
        assert pump(app, lambda: controller.engine.armed)
        assert requests == [1]
    finally:
        controller.shutdown()


def test_display_changes_stop_control_and_restart_on_the_new_layout(monkeypatch):
    from superhero import app as app_module
    from superhero.platform.displays import Display

    _app, controller = make_controller(monkeypatch)
    try:
        controller.start()
        backend = controller.engine.input = FakeInput()
        controller.engine.set_armed(True)
        generation = controller._generation
        new = [Display("DP-2", 0, 0, 2560, 1440, primary=True)]
        monkeypatch.setattr(app_module, "detect_displays", lambda: new)
        controller._on_screens_changed()
        assert not controller.engine.armed and backend.closed and controller.engine.input is None
        assert controller.displays == new and controller.display.name == "DP-2"
        assert controller._generation == generation + 1
    finally:
        controller.shutdown()


def test_replacing_the_voice_session_closes_the_old_microphone(monkeypatch):
    _app, controller = make_controller(monkeypatch)
    try:
        old = controller.voice
        closed = []
        monkeypatch.setattr(old, "close", lambda: closed.append(True))
        controller.rebuild_voice()
        assert closed == [True] and controller.voice is not old
    finally:
        controller.shutdown()


def test_preview_resumes_when_the_panel_is_reopened_on_the_camera_page(monkeypatch):
    _app, controller = make_controller(monkeypatch)
    try:
        controller.start()
        controller.tray_available = True
        controller.panel._nav.setCurrentRow(1)
        assert controller.pipeline.preview_enabled
        controller.panel.close()  # hides to the tray
        assert not controller.pipeline.preview_enabled
        controller.panel.show()
        assert controller.pipeline.preview_enabled
    finally:
        controller.shutdown()


def test_importing_a_bad_file_changes_nothing(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QPushButton

    from superhero import config as cfg
    from superhero.ui import panel as panel_module

    _app, controller = make_controller(monkeypatch)
    try:
        controller.config.pointer.size = 17
        controller.settings_changed(False)
        bad = tmp_path / "bad.json"
        bad.write_text("[not, a, settings, file")
        warnings = []
        monkeypatch.setattr(
            panel_module.QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), "")
        )
        monkeypatch.setattr(
            panel_module.QMessageBox, "warning", lambda *args: warnings.append(args[2])
        )
        button = next(
            b for b in controller.panel.findChildren(QPushButton) if b.text() == "Import…"
        )
        button.click()
        assert warnings and "Nothing was changed" in warnings[0]
        assert cfg.load("test").pointer.size == 17
        assert controller.config.pointer.size == 17
    finally:
        controller.shutdown()


def test_without_a_system_tray_closing_the_panel_quits(monkeypatch):
    """Stock GNOME has no tray: a hidden panel could never be reopened, nor the app quit."""
    _app, controller = make_controller(monkeypatch)
    try:
        controller.tray_available = False
        quits = []
        monkeypatch.setattr(controller, "quit", lambda: quits.append(True))
        controller.panel.show()
        assert controller.panel.close()
        assert quits == [True]
    finally:
        controller.shutdown()


def cli(monkeypatch, *args):
    """Run ``superhero ARGS`` in-process, keeping the environment it sets up to this test."""
    from superhero.app import main

    monkeypatch.setattr(sys, "argv", ["superhero", *args])
    monkeypatch.setenv("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", "offscreen"))
    monkeypatch.setenv("OPENCV_LOG_LEVEL", "FATAL")
    return main()


def test_cli_doctor_returns_the_report_status(monkeypatch):
    from superhero import doctor

    monkeypatch.setattr(doctor, "main", lambda: 3)
    assert cli(monkeypatch, "doctor") == 3


def test_cli_install_desktop_writes_the_entry(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert cli(monkeypatch, "install-desktop") == 0
    assert "Installed" in capsys.readouterr().out


def test_cli_bench_needs_a_readable_image(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli(monkeypatch, "bench")
    assert exit_info.value.code == 2 and "bench needs --image" in capsys.readouterr().err
    assert cli(monkeypatch, "bench", "--image", str(tmp_path / "missing.png")) == 2
    assert "Cannot read image" in capsys.readouterr().err


def test_cli_rejects_unknown_commands(monkeypatch):
    with pytest.raises(SystemExit):
        cli(monkeypatch, "fly")


def test_permission_granted_during_calibration_arms_only_after_it(monkeypatch):
    import threading
    from types import SimpleNamespace

    from superhero import app as app_module

    release = threading.Event()

    def slow_permission(_displays):
        release.wait(5)
        return FakeInput()

    monkeypatch.setattr(app_module, "create_input_backend", slow_permission)
    app, controller = make_controller(monkeypatch)
    try:
        controller.pipeline = SimpleNamespace(
            calibrated=True, stop=lambda: None, calibrate=lambda n: None
        )
        controller.set_armed(True)  # the permission dialog is up...
        controller.start_calibration()  # ...and the user starts calibrating meanwhile
        release.set()
        assert pump(app, lambda: controller.engine.input is not None)
        assert not controller.engine.armed, "winks would click during the calibration"
        controller._close_calibration()
        assert controller.engine.armed
    finally:
        controller.shutdown()


def test_control_switched_off_by_the_engine_stays_off_after_calibration(monkeypatch):
    from types import SimpleNamespace

    _app, controller = make_controller(monkeypatch)
    try:
        controller.pipeline = SimpleNamespace(
            calibrated=True, stop=lambda: None, calibrate=lambda n: None
        )
        backend = controller.engine.input = FakeInput()
        controller.set_armed(True)
        assert controller.engine.armed

        def broken(*_args):
            raise RuntimeError("portal session closed")

        backend.button = broken
        from fovea.events import TrackingStatus

        controller.engine.tracking = TrackingStatus.ACTIVE
        controller.engine.pointer, controller.engine._pointer_at = (0.5, 0.5), 1e12
        controller.engine.perform("left_click")  # the injection fails: control goes off
        assert not controller.engine.armed
        controller.start_calibration()
        controller._close_calibration()
        assert not controller.engine.armed

        controller.engine.perform("pause_toggle")  # the pause gesture is a choice too
        controller.start_calibration()
        controller._close_calibration()
        assert controller.engine.armed
    finally:
        controller.shutdown()


def test_all_displays_get_an_overlay_and_a_calibration_window(monkeypatch):
    from types import SimpleNamespace

    from superhero import app as app_module
    from superhero.platform.displays import ALL_DISPLAYS, Display

    main = Display("main", 0, 0, 1920, 1080, primary=True)
    right = Display("right", 1920, 0, 1280, 1024)
    monkeypatch.setattr(app_module, "detect_displays", lambda: [main, right])
    _app, controller = make_controller(monkeypatch)
    try:
        controller.config.active_display = ALL_DISPLAYS
        controller.restart_pipeline()
        assert controller.display.parts == (main, right)
        overlays = controller.overlay._children
        assert [o._display for o in overlays] == [main, right]

        controller.overlay.set_pointer(controller.display.normalize(2500, 500), True)
        assert overlays[1]._visible and not overlays[0]._visible
        controller.overlay.set_pointer(controller.display.normalize(100, 100), True)
        assert overlays[0]._visible and not overlays[1]._visible
        controller.overlay.set_status("Listening", True)
        assert overlays[0]._status == "Listening" and overlays[1]._status == ""

        controller.pipeline.stop()
        controller.pipeline = SimpleNamespace(calibrated=False, stop=lambda: None,
                                              calibrate=lambda n: None)  # fmt: skip
        controller.start_calibration()
        windows = controller._calibration
        assert [w.geometry().x() for w in windows] == [0, 1920]
        assert [w._texts for w in windows] == [True, False]  # instructions on the primary
        controller._close_calibration()
        assert controller._calibration is None
    finally:
        controller.shutdown()


def test_the_desktop_reader_is_optional(monkeypatch):
    from superhero import app as app_module
    from superhero.platform.accessibility import Control, DesktopUnavailable, Element, Window

    class FakeDesktop:
        can_locate = True

        def windows(self):
            return [Window("editor", "notes", focused=True)]

        def element_at(self, x, y):
            return Element("push button", 900, 500, 120, 30)

        def controls(self):
            return [Control("push button", "Save")]

        def press(self, name):
            return name == "Save"

        def close(self):
            pass

    monkeypatch.setattr(app_module, "open_desktop", FakeDesktop)
    _app, controller = make_controller(monkeypatch)
    try:
        assert controller.desktop_context() == (
            "Open windows, the focused one first:\n- editor: notes (focused)\n\n"
            'Controls in the focused window:\n- "Save" (push button)'
        )
        assert controller.press_control("Save") and not controller.press_control("Quit")
        controller.config.ai.share_windows = False
        assert controller.desktop_context() == ""
        assert controller.snap(controller.display, 0.5, 0.5) is None  # already on the button
    finally:
        controller.shutdown()

    def refused():
        raise DesktopUnavailable("no accessibility bus")

    monkeypatch.setattr(app_module, "open_desktop", refused)
    _app, controller = make_controller(monkeypatch)
    try:
        assert controller.desktop_context() == ""
        assert controller.snap(controller.display, 0.5, 0.5) is None
    finally:
        controller.shutdown()


def test_an_unavailable_desktop_reader_is_retried_later(monkeypatch):
    from superhero import app as app_module
    from superhero.platform.accessibility import DesktopUnavailable

    attempts = []

    def refused():
        attempts.append(1)
        raise DesktopUnavailable("Accessibility permission not granted")

    monkeypatch.setattr(app_module, "open_desktop", refused)
    _app, controller = make_controller(monkeypatch)
    try:
        controller.desktop_context()
        controller.desktop_context()
        assert attempts == [1]  # not retried at every request...
        controller._desktop_retry_at = 0.0  # ...but after a while (the permission granted)
        controller.desktop_context()
        assert attempts == [1, 1]
    finally:
        controller.shutdown()


def test_launch_on_start_writes_the_login_entry_for_this_profile(monkeypatch):
    from superhero.platform import launcher
    from superhero.ui.panel import Switch

    _app, controller = make_controller(monkeypatch)
    try:
        switch = next(
            s
            for s in controller.panel.findChildren(Switch)
            if s.accessibleName() == "Launch on start"
        )
        assert not switch.isChecked() and not launcher.autostart_enabled()
        switch.click()
        assert launcher.autostart_enabled()
        assert "--profile test" in launcher.autostart_path().read_text()  # not the default one
        switch.click()
        assert not launcher.autostart_enabled()
    finally:
        controller.shutdown()


def test_a_request_typed_in_the_panel_is_planned_but_not_run(monkeypatch):
    from superhero import app as app_module
    from superhero.ai.planner import Plan
    from superhero.interaction.commands import Command

    plan = Plan((Command("open", "https://example.org/"),), "Opens example.org.")
    monkeypatch.setattr(app_module, "make_plan", lambda request, config, desktop: plan)
    app, controller = make_controller(monkeypatch)
    try:
        results = []
        controller.ai_tried.connect(results.append)
        controller.try_ai("open example")
        deadline = time.monotonic() + 2
        while not results and time.monotonic() < deadline:
            app.processEvents()
        assert results == ["Opens example.org.\nopen example.org"]
        assert controller.engine.pending_plan is None
    finally:
        controller.shutdown()
