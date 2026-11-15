"""Optional hand tracking: MediaPipe hand landmarks and four deliberate hand poses.

Hands are a second model on the same camera frame (about 12 ms on the reference
machine). The pipeline runs it only when enabled, and only after the frame's
gaze events have been emitted, so it cannot add latency to the laser.

Landmark indices (MediaPipe): 0 wrist; thumb 1-4; index 5-8; middle 9-12;
ring 13-16; pinky 17-20. Each finger runs base -> tip.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fovea.events import Gesture, GesturePhase
from platformdirs import user_cache_dir

HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)
HAND_MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"

_FINGERS = {"index": (6, 8), "middle": (10, 12), "ring": (14, 16), "pinky": (18, 20)}  # (pip, tip)
_NS_PER_MS = 1_000_000
_PINCH_HOLD_MS = 60.0  # about two frames: enough to reject a single noisy frame


def download_hand_model(timeout: float = 30.0) -> Path:
    """Fetch the pinned hand model once and verify its checksum.

    ``timeout`` bounds each network wait, so a stalled connection fails with a reason
    instead of hanging; the caller retries the next time hand tracking starts.
    """
    path = Path(user_cache_dir("superhero")) / "models" / "hand_landmarker.task"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".partial")
        try:
            with (
                urllib.request.urlopen(HAND_MODEL_URL, timeout=timeout) as reply,
                tmp.open("wb") as out,
            ):
                shutil.copyfileobj(reply, out)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != HAND_MODEL_SHA256:
        path.unlink()
        raise RuntimeError("The hand model failed its checksum and was removed; try again.")
    return path


class HandTracker:
    """One hand's 21 landmarks per frame, or ``None``."""

    def __init__(self, model: Path) -> None:
        from mediapipe.tasks.python.core.base_options import BaseOptions
        from mediapipe.tasks.python.vision.core.vision_task_running_mode import (
            VisionTaskRunningMode,
        )
        from mediapipe.tasks.python.vision.hand_landmarker import (
            HandLandmarker,
            HandLandmarkerOptions,
        )

        self._landmarker = HandLandmarker.create_from_options(
            HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model)),
                running_mode=VisionTaskRunningMode.VIDEO,
                num_hands=1,
            )
        )
        self._timestamp_ms = -1

    def process(self, frame_bgr: Any, timestamp_ms: int) -> Sequence[Any] | None:
        import cv2
        import mediapipe as mp
        import numpy as np

        rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        self._timestamp_ms = max(self._timestamp_ms + 1, timestamp_ms)
        result = self._landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), self._timestamp_ms
        )
        return result.hand_landmarks[0] if result.hand_landmarks else None

    def close(self) -> None:
        self._landmarker.close()


def classify_pose(landmarks: Sequence[Any], aspect: float = 1.0) -> str | None:
    """Name the pose of a hand, or ``None`` for anything in between.

    ``aspect`` is frame width / height; landmarks are normalised per axis, so
    distances are compared in a square space. The test is rotation- and
    scale-invariant: a finger is extended when its tip is clearly farther from
    the wrist than its middle joint, curled when it is closer.
    """
    if len(landmarks) < 21:
        return None

    def dist(a: int, b: int) -> float:
        return math.hypot(
            (landmarks[a].x - landmarks[b].x) * aspect, landmarks[a].y - landmarks[b].y
        )

    size = dist(0, 9)  # wrist to middle knuckle
    if size < 1e-6:
        return None
    ratio = {name: dist(0, tip) / max(dist(0, pip), 1e-6) for name, (pip, tip) in _FINGERS.items()}
    extended = {name for name, r in ratio.items() if r > 1.15}
    curled = {name for name, r in ratio.items() if r < 0.95}
    others = {"middle", "ring", "pinky"}

    # Thumb and index tips touching while the other fingers stay open (an "OK" shape).
    # A fist also brings them together, hence the open-fingers condition. The gap is kept
    # well below what a relaxed open hand shows with the thumb resting alongside (~0.35).
    tip_gap = dist(4, 8) / size
    if tip_gap < 0.2 and len(extended & others) >= 2:
        return "hand_pinch"
    if len(curled) == 4:
        return "hand_fist"
    if len(extended) == 4 and tip_gap > 0.3:
        return "hand_open_palm"
    if extended == {"index", "middle"} and {"ring", "pinky"} <= curled:
        return "hand_two_fingers"
    return None


@dataclass
class HandGestureDetector:
    """Fire a pose once after it has been held; it must change before firing again."""

    hold_ms: float
    _pose: str | None = field(default=None, init=False)
    _since_ns: int = field(default=0, init=False)
    _fired: bool = field(default=False, init=False)

    @property
    def pose(self) -> str | None:
        """The pose currently seen, whether or not it has been held long enough to fire."""
        return self._pose

    def update(
        self, landmarks: Sequence[Any] | None, timestamp_ns: int, aspect: float = 1.0
    ) -> Gesture | None:
        pose = None if landmarks is None else classify_pose(landmarks, aspect)
        if pose != self._pose:
            self._pose, self._since_ns, self._fired = pose, timestamp_ns, False
            return None
        if pose is None or self._fired:
            return None
        needed = _PINCH_HOLD_MS if pose == "hand_pinch" else self.hold_ms
        if timestamp_ns - self._since_ns < needed * _NS_PER_MS:
            return None
        self._fired = True
        return Gesture(
            kind=pose, phase=GesturePhase.ENDED, confidence=1.0, timestamp_ns=timestamp_ns
        )
