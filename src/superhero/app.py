"""Application entry point and the controller that wires the layers together.

perception thread --events--> Controller (UI thread) --> InteractionEngine --> input adapter
                                    |--> Overlay (laser)    |--> VoiceSession
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import threading
import time

from fovea.events import CalibrationCue, CalibrationDone, Diagnostics, GazePoint, GazeTestDone
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
    QScreen,
)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from superhero import config as cfg
from superhero.ai.planner import AIUnavailable, make_plan, warm_up
from superhero.interaction.engine import InteractionEngine
from superhero.perception.pipeline import Pipeline, PipelineStatus
from superhero.platform.accessibility import (
    Desktop,
    DesktopUnavailable,
    describe_desktop,
    open_desktop,
    snap_point,
)
from superhero.platform.base import (
    APP_ID,
    create_input_backend,
    session_type,
)
from superhero.platform.displays import Display, detect_displays, pick_display
from superhero.platform.hotkey import register_hotkey
from superhero.platform.launcher import install_desktop_entry
from superhero.ui.calibration import CalibrationWindow, describe_error, quality_label
from superhero.ui.overlay import Overlays
from superhero.ui.panel import Panel
from superhero.voice.session import VoiceSession

log = logging.getLogger("superhero")

_DESKTOP_RETRY_S = 30.0
_CONTEXT_FRESH_S = 20.0  # the desktop read while the user spoke serves the request after

_PIPELINE_TEXT = {
    "starting": "Starting…",
    "running": "Running",
    "camera_error": "Camera unavailable",
    "camera_lost": "Camera disconnected — retrying",
    "model_error": "Tracking model failed",
    "stopped": "Stopped",
}
_VOICE_TEXT = {
    "listening": "●  Listening…",
    "preparing": "Preparing speech recognition — the first time downloads the model…",
    "transcribing": "Transcribing…",
}


class Controller(QObject):
    events = Signal(object, object, int)  # events, capture time, pipeline generation
    frame = Signal(object)
    input_ready = Signal(object, str)
    hotkey_ready = Signal(object, str)
    stop_requested = Signal()
    voice_toggle_requested = Signal()
    voice_arm_requested = Signal(bool)
    notice = Signal(str)
    engine_changed = Signal()
    ai_tried = Signal(str)  # the plan, or why there is none, for a request typed in the panel

    def __init__(
        self, profile: str, camera: object | None = None, *, global_hotkey: bool = True
    ) -> None:
        """``global_hotkey=False`` skips registering the system-wide emergency shortcut,
        for ``bench`` and tests, which must not touch the desktop session."""
        super().__init__()
        self.profile = profile
        self.config = cfg.load(profile)
        self._camera_override = camera
        self.displays = detect_displays()
        self.display = pick_display(self.displays, self.config.active_display)
        self.overlay = Overlays(self.config.pointer, self.display)
        self.engine = InteractionEngine(
            self.config,
            self.display,
            on_voice_toggle=self.voice_toggle_requested.emit,
            on_voice_arm=self.voice_arm_requested.emit,
            on_change=self.engine_changed.emit,
            on_notice=self.notice.emit,
            desktop_context=self.desktop_context,
            press=self.press_control,
            snap=self.snap,
        )
        # The accessibility reader, opened on first use from whichever thread needs it.
        self._desktop: Desktop | None = None
        self._desktop_retry_at = 0.0  # after a failure (say, a permission not yet granted)
        self._desktop_lock = threading.Lock()
        self._context: tuple[float, str] | None = None  # read ahead while the user speaks
        self._preparing = threading.Lock()
        self.pipeline: Pipeline | None = None
        # Events still queued from a pipeline that has been replaced are dropped by this.
        self._generation = 0
        self._opening_input = False
        self._calibration: list[CalibrationWindow] | None = None  # one per monitor
        self._hotkey = None
        self._want_armed = False
        self.quitting = False
        self._pipeline_state = PipelineStatus("starting")
        self._fps = 0.0
        self._calibration_text = (
            "Not calibrated yet. Run the calibration to make the laser accurate."
        )
        self._input_text = "Not requested yet"
        self._hotkey_text = "Registering…"
        self._flash = ""
        self._flash_timer = QTimer(self, singleShot=True, interval=2500)
        self._flash_timer.timeout.connect(lambda: self._set_flash(""))
        self._restart_timer = QTimer(self, singleShot=True, interval=400)
        self._restart_timer.timeout.connect(self.restart_pipeline)
        self._screens_timer = QTimer(self, singleShot=True, interval=500)
        self._screens_timer.timeout.connect(self._on_screens_changed)

        self.rebuild_voice()
        self.events.connect(self._on_events)
        self.frame.connect(self._on_frame)
        self.input_ready.connect(self._on_input_ready)
        self.hotkey_ready.connect(self._on_hotkey_ready)
        self.stop_requested.connect(self.emergency_stop)
        self.notice.connect(self._set_flash)
        self.engine_changed.connect(self._on_engine_changed)
        self.engine_changed.connect(self._update_overlay_status)
        self.voice_toggle_requested.connect(lambda: self.voice.toggle())
        self.voice_arm_requested.connect(lambda armed: self.voice.arm(armed))

        # Stock GNOME has no system tray. Without one, a hidden panel could never be reopened
        # and SUPERHERO could not be quit, so closing the panel quits instead (see Panel).
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.panel = Panel(self)
        self._build_tray()
        self._refresh = QTimer(self, interval=500)
        self._refresh.timeout.connect(self._refresh_status)
        self._refresh.start()
        self._watch_screens()
        if global_hotkey:
            threading.Thread(target=self._register_hotkey, daemon=True).start()
        else:
            self._hotkey_text = "Not registered in this mode"

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        self.overlay.show()
        self.panel.show()
        self.restart_pipeline()
        self.warm_ai()

    def restart_pipeline(self) -> None:
        if self.pipeline is not None:
            self.pipeline.stop()
        self.display = pick_display(self.displays, self.config.active_display)
        self.engine.display = self.display
        self.overlay.set_display(self.display)
        self._generation += 1
        generation = self._generation
        self.pipeline = Pipeline(
            self.config,
            self.display,
            lambda events, captured_ns: self.events.emit(events, captured_ns, generation),
            camera=self._camera_override,
            on_frame=lambda frame, _landmarks: self.frame.emit(frame),
        )
        self.pipeline.start()

    def quit(self) -> None:
        """Leave the application. Qt cancels a quit if any window refuses to close, and the
        panel normally does (it hides to the tray), so it has to be told this one is real."""
        self.quitting = True
        QApplication.quit()

    def shutdown(self) -> None:
        self.engine.set_armed(False)
        self.voice.close()
        if self.pipeline is not None:
            self.pipeline.stop()
        if self.engine.input is not None:
            self.engine.input.close()
        if self._hotkey is not None:
            self._hotkey.stop()
        with self._desktop_lock:
            if self._desktop is not None:
                self._desktop.close()
                self._desktop = None

    def _build_tray(self) -> None:
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#ff2d2d"))
        painter.setPen(QColor("#ffffff"))
        painter.drawEllipse(14, 14, 36, 36)
        painter.end()
        icon = QIcon(pixmap)
        QApplication.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        for text, slot in (
            ("Open SUPERHERO", lambda: (self.panel.show(), self.panel.raise_())),
            ("Pause / resume control", lambda: self.set_armed(not self.engine.armed)),
            ("Quit", self.quit),
        ):
            action = QAction(text, menu)
            action.triggered.connect(lambda _checked=False, s=slot: s())
            menu.addAction(action)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("SUPERHERO — camera tracking is active")
        self.tray.show()

    # -- arming and input ----------------------------------------------------------

    def set_armed(self, armed: bool) -> None:
        if armed and not (self.pipeline is not None and self.pipeline.calibrated):
            # An uncalibrated laser is a rough guess; clicking with it would hit the wrong things.
            self._set_flash("Calibrate first — open Calibration in the panel")
            self.engine.set_armed(False)
            return
        self._want_armed = armed
        if armed and self.engine.input is None:
            if not self._opening_input:  # one permission request at a time
                self._opening_input = True
                self._input_text = "Waiting for permission…"
                threading.Thread(target=self._open_input, daemon=True).start()
            return
        self.engine.set_armed(armed)

    def _open_input(self) -> None:
        try:
            self.input_ready.emit(create_input_backend(self.displays), "")
        except Exception as exc:
            log.warning("input backend unavailable: %s", exc)
            self.input_ready.emit(None, str(exc))

    def _on_input_ready(self, backend: object, error: str) -> None:
        self._opening_input = False
        if backend is None:
            self._input_text = f"Unavailable — {error}"
            self._want_armed = False
        else:
            self.engine.input = backend  # type: ignore[assignment]
            self._input_text = f"Ready ({backend.name})"  # type: ignore[attr-defined]
        # Permission can arrive while a calibration is open; closing it arms instead.
        self.engine.set_armed(self._want_armed and self._calibration is None)

    def _on_engine_changed(self) -> None:
        """The engine can switch control off (an injection error) or toggle it (the pause
        gesture or command). Remember that, so closing a calibration restores what the
        user last chose rather than an older choice."""
        if self._calibration is None and not self._opening_input:
            self._want_armed = self.engine.armed

    def emergency_stop(self) -> None:
        self._want_armed = False
        self.engine.emergency_stop()
        if self.voice.state == "listening":
            self.voice.toggle()
        self._set_flash("Control stopped")

    def _register_hotkey(self) -> None:
        try:
            hotkey = register_hotkey(self.config.emergency_hotkey, self.stop_requested.emit)
            self.hotkey_ready.emit(hotkey, "")
        except Exception as exc:
            self.hotkey_ready.emit(None, str(exc))

    def _on_hotkey_ready(self, hotkey: object, error: str) -> None:
        self._hotkey = hotkey
        self._hotkey_text = "Active" if hotkey is not None else f"Unavailable — {error}"

    # -- events ---------------------------------------------------------------------

    def _on_events(self, events: object, captured_ns: object, generation: int = 0) -> None:
        if generation != self._generation:
            return  # from a pipeline that was replaced (other display, camera or settings)
        saw_gaze = saw_frame = False
        for event in events:  # type: ignore[attr-defined]
            if isinstance(event, PipelineStatus):
                self._pipeline_state = event
                continue
            saw_frame = True
            if isinstance(event, Diagnostics):
                self._fps = event.fps
            elif isinstance(event, CalibrationCue | CalibrationDone | GazeTestDone):
                self._on_calibration_event(event)
            saw_gaze = saw_gaze or isinstance(event, GazePoint)
            self.engine.handle(event)
        if saw_frame:
            self._require_calibration()
        if self._pipeline_state.state != "running":
            self.overlay.set_pointer(None, False)
        elif saw_gaze:
            visible = self.engine.pointer_visible and self._calibration is None
            self.overlay.set_pointer(self.engine.pointer, visible, captured_ns)  # type: ignore[arg-type]
        elif not self.engine.pointer_confident:
            self.overlay.set_pointer(self.engine.pointer, False)

    def _require_calibration(self) -> None:
        """Keep the safety rule after a restart: no control through an uncalibrated mapping.

        Switching camera, resolution or display restarts the pipeline with that pair's own
        calibration, which may not exist yet.
        """
        if (
            self.engine.armed
            and self._calibration is None
            and self.pipeline is not None
            and not self.pipeline.calibrated
        ):
            self._want_armed = False
            self.engine.set_armed(False)
            self._set_flash("Control stopped — calibrate this camera and display first")

    def _on_frame(self, frame: object) -> None:
        if not self.panel.isVisible():
            return
        h, w = frame.shape[:2]  # type: ignore[attr-defined]
        rgb = frame[:, :, ::-1].copy()  # type: ignore[index]
        self.panel.show_frame(QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy())

    def set_preview(self, enabled: bool) -> None:
        if self.pipeline is not None:
            self.pipeline.preview_enabled = enabled

    # -- calibration ------------------------------------------------------------------

    def start_calibration(self) -> None:
        self._open_calibration()
        if self.pipeline is not None:
            self.pipeline.calibrate(self.config.calibration_points)

    def start_accuracy_test(self) -> None:
        if self.pipeline is None or not self.pipeline.calibrated:
            self._calibration_text = "Calibrate first, then measure accuracy."
            return
        self._open_calibration()
        self.pipeline.test_accuracy()

    def _open_calibration(self) -> None:
        if self._calibration is None:
            regions = list(self.display.parts) or [None]
            self._calibration = [CalibrationWindow(self.display, region) for region in regions]
            for window in self._calibration:
                window.cancelled.connect(
                    lambda: (self._close_calibration(), self.restart_pipeline())
                )
                window.finished.connect(self._close_calibration)
                window.test_requested.connect(
                    lambda: self.pipeline and self.pipeline.test_accuracy()
                )
                window.showFullScreen()
            main = next((w for w in self._calibration if w._texts), self._calibration[0])
            main.activateWindow()
        # After the windows exist, so this is not taken as the user's choice to stop.
        self.engine.set_armed(False)  # no gestures become clicks while calibrating

    def _close_calibration(self) -> None:
        if self._calibration is not None:
            for window in self._calibration:
                window.close()
            self._calibration = None
        self.engine.set_armed(self._want_armed)

    def _on_calibration_event(self, event: object) -> None:
        if isinstance(event, CalibrationDone):
            self._calibration_text = (
                f"{quality_label(event.loo_error)}. Estimated error "
                f"{describe_error(event.loo_error, self.display)} from {event.n_points} points."
            )
        elif isinstance(event, GazeTestDone):
            self._calibration_text = (
                f"{quality_label(event.median_error)}. Measured median error "
                f"{describe_error(event.median_error, self.display)}."
            )
        for window in self._calibration or ():
            window.on_event(event)

    # -- desktop accessibility -------------------------------------------------------------

    def _open_desktop(self) -> Desktop | None:
        with self._desktop_lock:
            if self._desktop is None and time.monotonic() >= self._desktop_retry_at:
                try:
                    self._desktop = open_desktop()
                except DesktopUnavailable as exc:
                    log.info("accessibility unavailable: %s", exc)
                    # Not at every click: the next try is in half a minute.
                    self._desktop_retry_at = time.monotonic() + _DESKTOP_RETRY_S
            return self._desktop

    def _read_desktop(self) -> str:
        """The open windows and the focused one's controls, as told to the AI."""
        if not self.config.ai.share_windows:
            return ""
        desktop = self._open_desktop()
        if desktop is None:
            return ""
        try:
            return describe_desktop(desktop.windows(), desktop.controls())
        except Exception:
            log.warning("could not read the desktop", exc_info=True)
            return ""

    def desktop_context(self) -> str:
        """The desktop for an AI request (speech thread). Empty if not shared.

        Read once ahead, while the user was still speaking, when that is recent enough.
        """
        cached, self._context = self._context, None
        if cached is not None and time.monotonic() - cached[0] <= _CONTEXT_FRESH_S:
            return cached[1]
        return self._read_desktop()

    def prepare_ai(self) -> None:
        """The user started to speak: read the desktop and load the model meanwhile, so the
        request is answered as soon as it is recognised."""
        if not self.config.ai.enabled or not self._preparing.acquire(blocking=False):
            return

        def run() -> None:
            try:
                self._context = (time.monotonic(), self._read_desktop())
                warm_up(self.config.ai)
            finally:
                self._preparing.release()

        threading.Thread(target=run, daemon=True).start()

    def warm_ai(self) -> None:
        """Load the AI model in the background, if AI commands are on."""
        if self.config.ai.enabled and self.config.ai.model:
            threading.Thread(target=warm_up, args=(self.config.ai,), daemon=True).start()

    def try_ai(self, request: str) -> None:
        """Plan a request typed in the panel and report the plan, without running it."""

        def run() -> None:
            try:
                plan = make_plan(request, self.config.ai, self._read_desktop())
                text = (
                    f"{plan.explanation}\n{plan.summary()}"
                    + ("\n⚠ It closes, deletes or sends something." if plan.risky else "")
                    if plan.steps
                    else plan.explanation or "The AI could not plan that."
                )
            except AIUnavailable as exc:
                text = str(exc)
            self.ai_tried.emit(text)

        threading.Thread(target=run, daemon=True).start()

    def press_control(self, name: str) -> bool:
        """Activate the focused window's control called ``name`` (an AI plan's step)."""
        desktop = self._open_desktop()
        return desktop is not None and desktop.press(name)

    def snap(self, display: Display, nx: float, ny: float) -> tuple[float, float] | None:
        """Where a click should land instead, from the accessibility tree (engine lock held)."""
        desktop = self._open_desktop()
        if desktop is None or not desktop.can_locate:
            return None
        try:
            radius = self.config.pointer.snap_radius
            return snap_point(desktop.element_at, display, nx, ny, radius)
        except Exception:
            log.warning("snapping failed; clicking where you look", exc_info=True)
            return None

    # -- voice ------------------------------------------------------------------------

    def rebuild_voice(self) -> None:
        old = getattr(self, "voice", None)
        if old is not None:
            old.close()  # an open microphone must not outlive its session
        self.voice = VoiceSession(self.config.voice, self.engine.handle_utterance)
        self.voice.state_changed.connect(self._on_voice_state)
        self.voice.result.connect(
            lambda text, outcome: self._set_flash(f"{outcome}: “{text}”" if text else outcome)
        )

    def _on_voice_state(self, state: str) -> None:
        if state in ("armed", "listening"):
            self.prepare_ai()
        self._update_overlay_status()

    def _set_flash(self, text: str) -> None:
        self._flash = text
        if text:
            self._flash_timer.start()
        self._update_overlay_status()

    def _update_overlay_status(self) -> None:
        state = self.voice.state
        if state == "error":
            text = "No microphone available"
        elif state == "listening":
            text = _VOICE_TEXT[state]
        else:  # the AI thinking or awaiting a yes, which happens while "transcribing"
            text = self.engine.pending_text or _VOICE_TEXT.get(state) or self._flash
        self.overlay.set_status(text, self._pipeline_state.state == "running")

    # -- displays -------------------------------------------------------------------------

    def _watch_screens(self) -> None:
        app = QGuiApplication.instance()
        if not isinstance(app, QGuiApplication):
            return  # no GUI application: nothing to watch
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._screens_changed)
        app.primaryScreenChanged.connect(self._screens_changed)
        for screen in QGuiApplication.screens():
            screen.geometryChanged.connect(self._screens_changed)

    def _on_screen_added(self, screen: QScreen) -> None:
        screen.geometryChanged.connect(self._screens_changed)
        self._screens_changed()

    def _screens_changed(self, *_args: object) -> None:
        self._screens_timer.start()  # a change usually arrives as several signals

    def _on_screens_changed(self) -> None:
        """Monitors were added, removed or rearranged: re-read them and start over safely."""
        displays = detect_displays()
        if not displays or displays == self.displays:
            return
        self.displays = displays
        if self.engine.armed:
            self._set_flash("Displays changed — control stopped; check the calibration")
        self._want_armed = False
        self.engine.set_armed(False)
        self._close_calibration()  # its targets were laid out on the old geometry
        if self.engine.input is not None:  # the portal shares monitors by geometry
            self.engine.input.close()
            self.engine.input = None
            self._input_text = "Not requested yet"
        self.panel.rebuild()
        self.restart_pipeline()

    # -- settings and profiles ----------------------------------------------------------

    def settings_changed(self, restart: bool) -> None:
        cfg.save(self.config, self.profile)
        self.overlay.update()
        if restart:
            self._restart_timer.start()

    def load_profile(self, name: str) -> None:
        self.profile = name
        loaded = cfg.load(name)
        # Keep object identity: the engine, overlay and voice session hold references.
        for section in ("camera", "tracking", "pointer", "gestures", "voice", "ai"):
            vars(getattr(self.config, section)).update(vars(getattr(loaded, section)))
        for name_ in ("active_display", "calibration_points", "emergency_hotkey"):
            setattr(self.config, name_, getattr(loaded, name_))
        self.panel.rebuild()
        self.settings_changed(True)

    def save_profile_as(self, name: str) -> None:
        self.profile = name
        cfg.save(self.config, name)
        self.panel.rebuild()

    def reset_profile(self) -> None:
        cfg.save(cfg.Config(), self.profile)
        self.load_profile(self.profile)

    # -- status -----------------------------------------------------------------------

    def latency(self) -> tuple[float, float] | None:
        values = sorted(self.overlay.latencies_ms)
        if len(values) < 20:
            return None
        return statistics.median(values), values[int(len(values) * 0.95)]

    def _refresh_status(self) -> None:
        self._update_overlay_status()
        if not self.panel.isVisible():
            return
        state = self._pipeline_state
        camera = _PIPELINE_TEXT.get(state.state, state.state)
        if state.detail:
            camera += f" — {state.detail}"
        camera_level = {
            "running": "ok", "camera_lost": "warn", "camera_error": "error", "model_error": "error",
        }.get(state.state, "idle")  # fmt: skip
        if state.state == "running" and 0 < self._fps < 0.7 * self.config.camera.fps:
            camera += f" — only {self._fps:.0f} fps; more light on your face usually helps"
            camera_level = "warn"

        running = state.state == "running"
        tracking = self.engine.tracking.value
        tracking_level = {"active": "ok", "uncertain": "warn", "lost": "error"}.get(
            tracking, "idle"
        )
        latency = self.latency()
        latency_text, latency_level = "Measuring…", "idle"
        if latency:
            latency_text = f"{latency[0]:.0f} ms typical, {latency[1]:.0f} ms at worst (95%)"
            latency_level = "ok" if latency[1] < 100 else "warn"
        calibrated = self.pipeline is not None and self.pipeline.calibrated
        input_level = (
            "ok" if self._input_text.startswith("Ready")
            else "error" if self._input_text.startswith("Unavailable")
            else "idle"
        )  # fmt: skip
        hotkey_level = (
            "ok"
            if self._hotkey_text == "Active"
            else ("warn" if self._hotkey_text.startswith("Unavailable") else "idle")
        )

        rows = [
            ("Camera", camera, camera_level),
            (
                "Tracking",
                f"{tracking.capitalize()} · {self._fps:.0f} fps" if running else "Not running",
                tracking_level if running else "idle",
            ),
            ("Pointer latency", latency_text, latency_level),
            ("Calibration", "Done" if calibrated else "Needed", "ok" if calibrated else "warn"),
            ("Desktop control", self._input_text, input_level),
            ("Emergency shortcut", self._hotkey_text, hotkey_level),
            ("Last gesture", cfg.GESTURES.get(self.engine.last_gesture, "None yet"), "idle"),
            *self._signal_rows(),
        ]
        if self.engine.armed:
            detail = "Look to point. Your gestures click, scroll and talk."
        elif input_level == "error":
            detail = "SUPERHERO cannot control this desktop — see Desktop control below."
        elif not calibrated:
            detail = "Calibrate first, so the laser lands where you look."
        else:
            detail = "Start control to let your gestures click, scroll and type."
        self.panel.refresh(
            self.engine.armed,
            rows,
            (self._calibration_text, "ok" if calibrated else "warn"),
            detail,
        )

    def _signal_rows(self) -> list[tuple[str, str, str]]:
        """Live detector inputs, so a gesture that will not fire can be diagnosed by eye."""
        signals = self.pipeline.signals if self.pipeline is not None else {}
        tracking, gestures = self.config.tracking, self.config.gestures
        face = signals.get("face")
        rows = [("Face position", *face) if face else ("Face position", "No face in view", "idle")]
        if tracking.mouth:
            jaw = signals.get("jaw_open")
            bound = cfg.ACTIONS.get(gestures.bindings.get("mouth_open", "none"), "")
            if jaw is None:
                rows.append(("Mouth opening", "No face in view", "idle"))
            else:
                over = jaw >= gestures.mouth_open_threshold
                rows.append((
                    "Mouth opening",
                    f"{jaw:.2f} now · fires at {gestures.mouth_open_threshold:.2f} held "
                    f"{gestures.mouth_open_ms / 1000:.1f} s → {bound}",
                    "ok" if over else "idle",
                ))  # fmt: skip
        else:
            rows.append(("Mouth opening", "Off — turn on mouth tracking under Tracking", "idle"))
        if tracking.hand:
            hand = str(signals.get("hand", "Starting…"))
            pose = cfg.GESTURES.get(hand)
            rows.append(("Hand", pose or hand.capitalize(), "ok" if pose else "idle"))
        else:
            rows.append(("Hand", "Off — turn on hand tracking under Tracking", "idle"))
        return rows


def bench(image_path: str, seconds: float) -> int:
    """Run the full pipeline on a still image and report capture-to-paint latency."""
    import cv2

    from superhero.perception.camera import StillCamera

    image = cv2.imread(image_path)
    if image is None:
        print(f"Cannot read image: {image_path}", file=sys.stderr)
        return 2
    image = cv2.resize(image, (640, int(image.shape[0] * 640 / image.shape[1])))
    app = QApplication(sys.argv[:1])
    controller = Controller("bench", camera=StillCamera(image), global_hotkey=False)
    controller.overlay.show()
    controller.restart_pipeline()
    QTimer.singleShot(int(seconds * 1000), app.quit)
    app.exec()
    values = sorted(controller.overlay.latencies_ms)
    controller.shutdown()
    if not values:
        print("No gaze points were produced (no face found in the image?).", file=sys.stderr)
        return 1
    p50, p95 = statistics.median(values), values[int(len(values) * 0.95)]
    print(
        f"frames={len(values)} fps={controller._fps:.1f} capture-to-paint p50={p50:.1f}ms p95={p95:.1f}ms max={values[-1]:.1f}ms"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="superhero", description="Multimodal desktop control.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["run", "doctor", "install-desktop", "bench", "pen-demo"],
        default="run",
    )
    parser.add_argument("--profile", default=cfg.DEFAULT_PROFILE)
    parser.add_argument("--image", help="bench: a photo of a face to use instead of a camera")
    parser.add_argument("--seconds", type=float, default=10.0, help="bench: duration")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "doctor":
        from superhero.doctor import main as doctor

        return doctor()
    if args.command == "pen-demo":
        from superhero.perception.pen import run_demo

        return run_demo(cfg.load(args.profile).camera.index, max(args.seconds, 30.0))
    if args.command == "install-desktop":
        print(f"Installed {install_desktop_entry()}")
        return 0
    # A native Wayland client cannot place a window or keep it above others, and the
    # laser needs both. Running through XWayland restores them.
    os.environ.setdefault("OPENCV_LOG_LEVEL", "FATAL")  # a missing camera is reported in the UI
    if session_type() == "wayland" and os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    if args.command == "bench":
        if not args.image:
            parser.error("bench needs --image")
        return bench(args.image, args.seconds)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("SUPERHERO")
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(False)
    controller = Controller(args.profile)
    app.aboutToQuit.connect(controller.shutdown)
    controller.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
