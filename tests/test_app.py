"""The whole application, offscreen, with recorded landmarks instead of a camera and model."""

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
