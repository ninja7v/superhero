"""The perception thread: camera -> landmarks -> gaze and gesture events.

This composes Fovea's capture session, landmark estimator and frame processor
rather than its ``WebcamEventSource`` so that one landmark inference per frame
feeds everything: Fovea's gaze/blink pipeline, the mouth and head detectors
here, and the preview. Everything downstream sees only events.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any

from fovea.events import GazePoint
from fovea.webcam.calibration import CalibrationIdentity, CalibrationTarget
from fovea.webcam.capture import CaptureClosed, CaptureSession, FrameCapture
from fovea.webcam.engine import GazeEngine, GazeSettings
from fovea.webcam.features import estimate_head_pose
from fovea.webcam.frame_processor import GazeFrameProcessor
from fovea.webcam.landmarks import FaceLandmarkEstimator
from fovea.webcam.model import download_face_landmarker

from superhero.config import Config, calibration_path
from superhero.perception.camera import Camera
from superhero.perception.face_gestures import HeadGestureDetector, MouthOpenDetector
from superhero.perception.hands import HandGestureDetector, HandTracker, download_hand_model
from superhero.platform.displays import Display

log = logging.getLogger(__name__)

_LOST_AFTER_S = 2.0
_RECONNECT_EVERY_S = 1.0
_MODEL_RETRY_S = 5.0
_PREVIEW_INTERVAL_S = 1 / 15


@dataclass(frozen=True, slots=True)
class PipelineStatus:
    """Lifecycle of the perception thread, for status displays."""

    state: str  # starting | running | camera_error | camera_lost | model_error | stopped
    detail: str = ""


def calibration_targets(points: int) -> tuple[CalibrationTarget, ...]:
    """5-, 9- or 13-point layouts in display-normalized coordinates."""
    lo, mid, hi = 0.08, 0.5, 0.92
    corners = [(lo, lo), (hi, lo), (lo, hi), (hi, hi)]
    edges = [(mid, lo), (lo, mid), (hi, mid), (mid, hi)]
    inner = [(0.29, 0.29), (0.71, 0.29), (0.29, 0.71), (0.71, 0.71)]
    layout = [(mid, mid), *corners]
    if points >= 9:
        layout += edges
    if points >= 13:
        layout += inner
    return tuple(CalibrationTarget(f"p{i}", x, y) for i, (x, y) in enumerate(layout))


def gaze_settings(config: Config, display: Display) -> GazeSettings:
    g, p = config.gestures, config.pointer
    return GazeSettings(
        one_euro_mincutoff=p.smoothing_mincutoff,
        one_euro_beta=p.smoothing_beta,
        wink_min_ms=g.wink_min_ms,
        wink_max_ms=g.wink_max_ms,
        double_blink_ms=g.double_blink_ms,
        long_blink_ms=g.long_blink_ms,
        calibration_path=calibration_path(display.name, config.camera.index),
        debug=False,
    )


class Pipeline:
    """Runs perception on its own thread and hands events to ``emit``.

    ``emit(events, captured_ns)`` is called from the perception thread once per
    frame; ``captured_ns`` is the monotonic capture time of the frame (None when
    no frame was read) so the receiver can measure capture-to-display latency.
    """

    def __init__(
        self,
        config: Config,
        display: Display,
        emit: Callable[[Sequence[object], int | None], None],
        *,
        camera: FrameCapture | None = None,
        on_frame: Callable[[Any, Any], None] | None = None,
    ) -> None:
        self._config = config
        self._display = display
        self._emit = emit
        self._camera = camera or Camera(
            config.camera.index,
            config.camera.width,
            config.camera.height,
            config.camera.fps,
            config.camera.mirror,
        )
        self._on_frame = on_frame
        self.preview_enabled = False
        self.calibrated = False
        # Latest raw signals, for the status page: what the detectors see before thresholds.
        self.signals: dict[str, object] = {}
        self._commands: SimpleQueue[Callable[[GazeEngine], None]] = SimpleQueue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Hand tracking: the model file arrives from a loader thread; the tracker itself is
        # created and used only on the perception thread.
        self._hand_model: Path | None = None
        self._hands: HandTracker | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="superhero-perception", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def calibrate(self, points: int) -> None:
        targets = calibration_targets(points)
        self._commands.put(lambda engine: engine.start_calibration(targets))

    def test_accuracy(self) -> None:
        self._commands.put(lambda engine: engine.start_gaze_test())

    # -- thread body ---------------------------------------------------------

    def _status(self, state: str, detail: str = "") -> None:
        self._emit((PipelineStatus(state, detail),), None)

    def _connect(self) -> bool:
        connect = getattr(self._camera, "connect", None)
        if connect is None:
            return True
        try:
            connect()
            return True
        except Exception as exc:
            self._status("camera_error", str(exc))
            return False

    def _disconnect(self) -> None:
        disconnect = getattr(self._camera, "disconnect", None)
        if disconnect is not None:
            disconnect()

    def _load_estimator(self) -> FaceLandmarkEstimator | None:
        """The face model, retried until it loads: a first run offline recovers by itself."""
        while not self._stop.is_set():
            try:
                return FaceLandmarkEstimator(download_face_landmarker())
            except Exception as exc:
                log.warning("landmark model unavailable: %s", exc)
                self._status(
                    "model_error", f"{exc} (the face model downloads once; retrying in 5 s)"
                )
                self._stop.wait(_MODEL_RETRY_S)
        return None

    def _fetch_hand_model(self) -> None:
        """Loader thread: download the hand model without holding up gaze."""
        try:
            self._hand_model = download_hand_model()
        except Exception as exc:  # hands are optional: gaze keeps running
            log.warning("hand tracking unavailable: %s", exc)
            self.signals["hand"] = f"unavailable — {exc}"

    def _hand_tracker(self) -> HandTracker | None:
        """The hand tracker once its model is ready (perception thread only)."""
        if self._hands is None and self._hand_model is not None:
            model, self._hand_model = self._hand_model, None
            try:
                self._hands = HandTracker(model)
            except Exception as exc:
                log.exception("hand model failed to load")
                self.signals["hand"] = f"unavailable — {exc}"
        return self._hands

    def _run(self) -> None:
        self._status("starting")
        if self._config.tracking.hand:
            self.signals["hand"] = "loading the hand model…"
            threading.Thread(
                target=self._fetch_hand_model, name="superhero-hand-model", daemon=True
            ).start()
        estimator = self._load_estimator()
        if estimator is None:
            self._status("stopped")
            return
        try:
            while not self._stop.is_set():
                if self._connect():
                    try:
                        self._track(estimator)
                    except Exception as exc:  # report, then start over rather than die
                        log.exception("perception failed; restarting it")
                        self._status("model_error", f"Tracking stopped: {exc}; restarting")
                    finally:
                        self._disconnect()
                self._stop.wait(_RECONNECT_EVERY_S)
        finally:
            estimator.close()
            if self._hands is not None:
                self._hands.close()
            self._status("stopped")

    def _track(self, estimator: FaceLandmarkEstimator) -> None:
        """Process frames until stopped or the camera stays silent too long."""
        config, cam = self._config, self._camera
        width = getattr(cam, "actual_width", config.camera.width)
        height = getattr(cam, "actual_height", config.camera.height)
        identity = CalibrationIdentity(
            display_id=self._display.name,
            display_width=self._display.width,
            display_height=self._display.height,
            camera_index=config.camera.index,
            frame_width=width,
            frame_height=height,
        )
        settings = gaze_settings(config, self._display)
        engine = GazeEngine(settings, identity=identity)
        processor = GazeFrameProcessor(engine, settings)
        mouth = MouthOpenDetector(
            config.gestures.mouth_open_threshold, config.gestures.mouth_open_ms
        )
        head = HeadGestureDetector(
            config.gestures.head_tilt_deg,
            config.gestures.head_nod_deg,
            roll_sign=-1.0 if config.camera.mirror else 1.0,
        )
        hand_gestures = HandGestureDetector(config.gestures.hand_hold_ms)
        session = CaptureSession(cam, max_fps=config.camera.fps)
        session.start()
        self._status("running", f"{width}x{height}")

        latency_window: deque[float] = deque(maxlen=90)
        last = time.perf_counter()
        fps_t0, fps_n, fps = last, 0, 0.0
        last_diag = last_preview = 0.0
        silent_since: float | None = None
        try:
            while not self._stop.is_set():
                self._drain_commands(engine)
                self.calibrated = engine.model is not None
                try:
                    captured = session.next_frame()
                except CaptureClosed:
                    return
                now = time.perf_counter()
                dt, last = max(1e-3, now - last), now
                fps_n += 1
                if now - fps_t0 >= 0.5:
                    fps, fps_t0, fps_n = fps_n / (now - fps_t0), now, 0
                diag_due = now - last_diag >= 0.5
                if diag_due:
                    last_diag = now
                diag = {
                    "diagnostics_due": diag_due,
                    "latency_window": latency_window,
                    "dropped_frames": session.dropped_frames,
                }

                if captured is None:
                    silent_since = silent_since or now
                    events = processor.events_for_lost_frame(fps, time.time_ns(), **diag)
                    self._emit(events, None)
                    if now - silent_since > _LOST_AFTER_S:
                        self._status("camera_lost", "No frames from the camera; reconnecting.")
                        return
                    continue
                silent_since = None

                frame = captured.pixels
                h, w = frame.shape[:2]
                observation = estimator.process(frame)
                landmarks = None if observation is None else observation.landmarks
                blendshapes = None if observation is None else observation.blendshapes
                events: list[object] = list(
                    processor.events_for_frame(
                        landmarks,
                        float(w),
                        float(h),
                        dt,
                        fps,
                        captured.timestamp_ns,
                        blendshapes,
                        **diag,
                    )
                )
                if not config.tracking.eye:
                    events = [e for e in events if not isinstance(e, GazePoint)]
                jaw_open = None if observation is None else (blendshapes or {}).get("jawopen", 0.0)
                self.signals["jaw_open"] = jaw_open
                if observation is None or landmarks is None or jaw_open is None:
                    mouth.reset()
                    head.reset()
                elif engine.wizard is None:
                    if config.tracking.mouth:
                        gesture = mouth.update(jaw_open, captured.timestamp_ns)
                        if gesture is not None:
                            events.append(gesture)
                    if config.tracking.head:
                        _yaw, pitch, roll = estimate_head_pose(landmarks, float(w), float(h))
                        events.extend(head.update(pitch, roll, captured.timestamp_ns))

                latency_ms = (time.monotonic_ns() - captured.captured_ns) / 1e6
                latency_window.append(latency_ms)
                events = [
                    replace(e, latency_ms=latency_ms) if isinstance(e, GazePoint) else e
                    for e in events
                ]
                self._emit(events, captured.captured_ns)

                # Hands run after the gaze events are out, so they never delay the laser.
                hands = self._hand_tracker() if config.tracking.hand else None
                if hands is not None and engine.wizard is None:
                    hand = hands.process(frame, captured.timestamp_ns // 1_000_000)
                    gesture = hand_gestures.update(hand, captured.timestamp_ns, w / h)
                    self.signals["hand"] = (
                        "no hand in view"
                        if hand is None
                        else (hand_gestures.pose or "hand seen, no known pose")
                    )
                    if gesture is not None:
                        self._emit((gesture,), None)

                if (
                    self.preview_enabled
                    and self._on_frame
                    and now - last_preview >= _PREVIEW_INTERVAL_S
                ):
                    last_preview = now
                    self._on_frame(frame, landmarks)
        finally:
            session.stop()

    def _drain_commands(self, engine: GazeEngine) -> None:
        while True:
            try:
                self._commands.get_nowait()(engine)
            except Empty:
                return
