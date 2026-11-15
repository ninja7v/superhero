"""The perception thread, driven by recorded landmarks instead of a camera and model."""

import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fovea.events import CalibrationCue, GazePoint, Gesture, GesturePhase, TrackingState
from fovea.webcam.backend import LandmarkObservation
from fovea.webcam.fixtures import read_landmark_frames

from superhero.config import Config
from superhero.perception import pipeline as pipeline_module
from superhero.perception.pipeline import Pipeline, PipelineStatus, calibration_targets
from superhero.platform.displays import Display

FRAMES = list(read_landmark_frames(Path(__file__).parent / "fixtures" / "frontal_30f.jsonl"))
DISPLAY = Display("main", 0, 0, 1920, 1080, primary=True)


class ReplayEstimator:
    """Stands in for the landmark model: replays the recorded face forever."""

    jaw_open = 0.0
    face_visible = True

    def __init__(self, _model_path):
        self._i = 0

    def process(self, _frame):
        if not ReplayEstimator.face_visible:
            return None
        frame = FRAMES[self._i % len(FRAMES)]
        self._i += 1
        return LandmarkObservation(
            frame.landmarks, {**frame.blendshapes, "jawopen": ReplayEstimator.jaw_open}
        )

    def close(self):
        pass


class FakeCamera:
    def __init__(self):
        self.working = True
        self.connects = 0

    def connect(self):
        self.connects += 1

    def disconnect(self):
        pass

    def read(self):
        time.sleep(0.004)
        return np.zeros((48, 64, 3), np.uint8) if self.working else None


class Collector:
    def __init__(self):
        self.events, self.captures = [], []
        self._lock = threading.Lock()

    def __call__(self, events, captured_ns):
        with self._lock:
            self.events.extend(events)
            self.captures.append(captured_ns)

    def of(self, kind):
        with self._lock:
            return [e for e in self.events if isinstance(e, kind)]

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(pipeline_module, "FaceLandmarkEstimator", ReplayEstimator)
    monkeypatch.setattr(pipeline_module, "download_face_landmarker", lambda: Path("unused"))
    monkeypatch.setattr(pipeline_module, "_LOST_AFTER_S", 0.2)
    monkeypatch.setattr(pipeline_module, "_RECONNECT_EVERY_S", 0.05)
    ReplayEstimator.jaw_open, ReplayEstimator.face_visible = 0.0, True
    config, camera, sink = Config(), FakeCamera(), Collector()
    config.camera.fps = 240
    config.gestures.mouth_open_ms = 80
    pipeline = Pipeline(config, DISPLAY, sink, camera=camera)
    yield pipeline, config, camera, sink
    pipeline.stop()


def test_emits_gaze_with_latency_and_tracking_state(rig):
    pipeline, config, _camera, sink = rig
    pipeline.start()
    assert sink.wait_for(lambda: len(sink.of(GazePoint)) >= 10)
    gaze = sink.of(GazePoint)[-1]
    assert 0.0 <= gaze.x <= 1.0 and 0.0 <= gaze.y <= 1.0
    assert gaze.latency_ms is not None and 0.0 <= gaze.latency_ms < 100.0
    assert sink.of(TrackingState)
    size = f"{config.camera.width}x{config.camera.height}"
    assert PipelineStatus("running", size) in sink.of(PipelineStatus)
    assert any(c is not None for c in sink.captures)


def test_eye_tracking_can_be_switched_off(rig):
    pipeline, config, _camera, sink = rig
    config.tracking.eye = False
    pipeline.start()
    assert sink.wait_for(lambda: len(sink.of(TrackingState)) >= 10)
    assert sink.of(GazePoint) == []


def test_mouth_gesture_comes_from_the_same_landmarks(rig):
    pipeline, _config, _camera, sink = rig
    ReplayEstimator.jaw_open = 0.8
    pipeline.start()
    assert sink.wait_for(lambda: any(g.phase is GesturePhase.ENDED for g in sink.of(Gesture)))
    assert {g.kind for g in sink.of(Gesture)} == {"mouth_open"}


def test_mouth_tracking_can_be_switched_off(rig):
    pipeline, config, _camera, sink = rig
    config.tracking.mouth = False
    ReplayEstimator.jaw_open = 0.8
    pipeline.start()
    assert sink.wait_for(lambda: len(sink.of(GazePoint)) >= 40)
    assert sink.of(Gesture) == []


def test_calibration_request_produces_cues(rig):
    pipeline, _config, _camera, sink = rig
    pipeline.start()
    assert sink.wait_for(lambda: len(sink.of(GazePoint)) >= 5)
    ReplayEstimator.jaw_open = 0.8
    pipeline.calibrate(5)
    assert sink.wait_for(lambda: bool(sink.of(CalibrationCue)))
    cue = sink.of(CalibrationCue)[0]
    assert (cue.x, cue.y, cue.total) == (0.5, 0.5, 5)


def test_calibration_completes_and_is_saved_per_display(rig):
    from superhero.config import calibration_path

    pipeline, _config, _camera, sink = rig
    pipeline.start()
    pipeline.calibrate(5)
    assert sink.wait_for(lambda: pipeline.calibrated and bool(sink.of(CalibrationCue)), timeout=10)
    assert calibration_path("main", 0).is_file()


def test_camera_loss_is_reported_and_recovers(rig):
    pipeline, _config, camera, sink = rig
    pipeline.start()
    assert sink.wait_for(lambda: len(sink.of(GazePoint)) >= 5)
    camera.working = False
    assert sink.wait_for(lambda: any(s.state == "camera_lost" for s in sink.of(PipelineStatus)))
    seen = len(sink.of(GazePoint))
    camera.working = True
    assert sink.wait_for(lambda: len(sink.of(GazePoint)) > seen + 5)
    assert camera.connects >= 2


def test_no_face_means_no_gaze(rig):
    pipeline, _config, _camera, sink = rig
    ReplayEstimator.face_visible = False
    pipeline.start()
    assert sink.wait_for(lambda: len(sink.of(TrackingState)) >= 10)
    assert sink.of(GazePoint) == []
    assert all(s.status.value == "lost" for s in sink.of(TrackingState))


def test_camera_that_cannot_open_reports_an_error_and_keeps_retrying(rig):
    pipeline, _config, camera, sink = rig
    camera.connect = lambda: (_ for _ in ()).throw(RuntimeError("Could not open webcam index 0"))
    pipeline.start()
    assert sink.wait_for(
        lambda: len([s for s in sink.of(PipelineStatus) if s.state == "camera_error"]) >= 2
    )


def test_hand_gestures_arrive_without_delaying_gaze(rig, monkeypatch):
    from tests.test_hands import ALL, hand

    class FakeHands:
        def __init__(self, _model):
            pass

        def process(self, _frame, _timestamp_ms):
            return hand(extended=ALL)

        def close(self):
            pass

    monkeypatch.setattr(pipeline_module, "HandTracker", FakeHands)
    monkeypatch.setattr(pipeline_module, "download_hand_model", lambda: Path("unused"))
    pipeline, config, _camera, sink = rig
    config.tracking.hand = True
    config.gestures.hand_hold_ms = 100
    pipeline.start()
    assert sink.wait_for(lambda: bool(sink.of(Gesture)))
    assert [g.kind for g in sink.of(Gesture)] == ["hand_open_palm"]
    # The gesture is emitted in its own batch, after that frame's gaze point.
    gesture_at = sink.events.index(sink.of(Gesture)[0])
    assert any(isinstance(e, GazePoint) for e in sink.events[:gesture_at])


def test_hand_model_failure_does_not_stop_gaze(rig, monkeypatch):
    def broken():
        raise RuntimeError("no network")

    monkeypatch.setattr(pipeline_module, "download_hand_model", broken)
    pipeline, config, _camera, sink = rig
    config.tracking.hand = True
    pipeline.start()
    assert sink.wait_for(lambda: len(sink.of(GazePoint)) >= 10)


@pytest.mark.parametrize("points", [5, 9, 13])
def test_calibration_layouts(points):
    targets = calibration_targets(points)
    assert len(targets) == points
    assert len({(t.x, t.y) for t in targets}) == points
    assert all(0.05 <= t.x <= 0.95 and 0.05 <= t.y <= 0.95 for t in targets)
    xs, ys = [t.x for t in targets], [t.y for t in targets]
    assert max(xs) - min(xs) > 0.8 and max(ys) - min(ys) > 0.8  # covers the display


def test_a_stalled_hand_model_download_does_not_hold_up_gaze(rig, monkeypatch):
    release = threading.Event()

    def stalled():
        release.wait(10)
        raise RuntimeError("network timed out")

    monkeypatch.setattr(pipeline_module, "download_hand_model", stalled)
    pipeline, config, _camera, sink = rig
    config.tracking.hand = True
    pipeline.start()
    try:
        assert sink.wait_for(lambda: len(sink.of(GazePoint)) >= 10)
        assert pipeline.signals["hand"] == "loading the hand model…"
    finally:
        release.set()
    assert sink.wait_for(lambda: "network timed out" in str(pipeline.signals.get("hand")))


def test_hand_tracking_attaches_once_its_model_arrives(rig, monkeypatch):
    from tests.test_hands import ALL, hand

    arrived = threading.Event()

    class FakeHands:
        def __init__(self, _model):
            pass

        def process(self, _frame, _timestamp_ms):
            return hand(extended=ALL)

        def close(self):
            pass

    def slow_download():
        arrived.wait(10)
        return Path("hand.task")

    monkeypatch.setattr(pipeline_module, "HandTracker", FakeHands)
    monkeypatch.setattr(pipeline_module, "download_hand_model", slow_download)
    pipeline, config, _camera, sink = rig
    config.tracking.hand = True
    config.gestures.hand_hold_ms = 100
    pipeline.start()
    assert sink.wait_for(lambda: len(sink.of(GazePoint)) >= 5)
    assert sink.of(Gesture) == []
    arrived.set()
    assert sink.wait_for(lambda: [g.kind for g in sink.of(Gesture)] == ["hand_open_palm"])


def test_a_missing_face_model_is_retried_until_it_loads(rig, monkeypatch):
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("no internet connection")
        return Path("unused")

    monkeypatch.setattr(pipeline_module, "download_face_landmarker", flaky)
    monkeypatch.setattr(pipeline_module, "_MODEL_RETRY_S", 0.05)
    pipeline, _config, _camera, sink = rig
    pipeline.start()
    assert sink.wait_for(lambda: len(sink.of(GazePoint)) >= 5)
    errors = [s for s in sink.of(PipelineStatus) if s.state == "model_error"]
    assert len(errors) == 2 and "no internet connection" in errors[0].detail


def test_an_unexpected_error_restarts_tracking_instead_of_ending_it(rig, monkeypatch):
    calls = []
    original = ReplayEstimator.process

    def once_broken(self, frame):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("inference failed")
        return original(self, frame)

    monkeypatch.setattr(ReplayEstimator, "process", once_broken)
    pipeline, _config, _camera, sink = rig
    pipeline.start()
    assert sink.wait_for(lambda: any(s.state == "model_error" for s in sink.of(PipelineStatus)))
    seen = len(sink.of(GazePoint))
    assert sink.wait_for(lambda: len(sink.of(GazePoint)) > seen + 5)
