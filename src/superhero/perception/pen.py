"""EXPERIMENTAL pen "wand" detection — a proof of concept, not wired into the pointer.

Idea: an ordinary pen is a long straight object passing through the grip of a
tracked hand. Find the hand (MediaPipe), look for long straight edges near the
thumb/index grip, and take the end pointing away from the wrist as the tip.

Known limits, by construction:
* A single camera sees where the tip *is* in the image, not where the pen
  *points* in space. This estimates tip position, so it behaves like moving a
  marker in the air, not like aiming a wand.
* Any long straight edge through the grip is accepted: a ruler, a chopstick,
  a sleeve seam, a line on the wall behind the hand.
* A pen pointed at the camera is foreshortened to a dot and disappears.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class PenEstimate:
    tip: tuple[float, float]  # pixels
    tail: tuple[float, float]
    length_ratio: float  # visible pen length in hand sizes


def detect_pen(frame_bgr: Any, hand: Sequence[Any]) -> PenEstimate | None:
    """Find a pen held in ``hand`` (21 normalised MediaPipe landmarks)."""
    import cv2

    if len(hand) < 21:
        return None
    height, width = frame_bgr.shape[:2]

    def px(i: int) -> np.ndarray:
        return np.array([hand[i].x * width, hand[i].y * height])

    wrist = px(0)
    grip = (px(4) + px(8)) / 2  # between thumb tip and index tip
    size = float(np.linalg.norm(px(9) - wrist))
    if size < 8:
        return None

    reach = int(4 * size)
    x0, y0 = max(0, int(grip[0]) - reach), max(0, int(grip[1]) - reach)
    x1, y1 = min(width, int(grip[0]) + reach), min(height, int(grip[1]) + reach)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    grey = cv2.cvtColor(frame_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(grey, (5, 5), 0), 50, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=30, minLineLength=int(size), maxLineGap=int(size * 0.25)
    )
    if lines is None:
        return None

    best: PenEstimate | None = None
    outward = grip - wrist
    for ax, ay, bx, by in lines.reshape(-1, 4):
        a, b = np.array([ax + x0, ay + y0], float), np.array([bx + x0, by + y0], float)
        length = float(np.linalg.norm(b - a))
        # Perpendicular distance from the grip to the segment's line.
        along, across = b - a, grip - a
        offset = abs(float(along[0] * across[1] - along[1] * across[0])) / length
        if offset > 0.45 * size:
            continue
        tip, tail = (a, b) if np.linalg.norm(a - wrist) > np.linalg.norm(b - wrist) else (b, a)
        # The pen must stick out beyond the fingers, on the finger side of the wrist.
        # This is what rejects the forearm, whose edges also run straight through the hand.
        if np.linalg.norm(tip - wrist) < 1.6 * size or float(np.dot(tip - wrist, outward)) <= 0:
            continue
        if best is None or length / size > best.length_ratio:
            best = PenEstimate(
                (float(tip[0]), float(tip[1])), (float(tail[0]), float(tail[1])), length / size
            )
    return best


class TipCalibration:
    """Least-squares affine map from tip position in the image to a point on the display."""

    def __init__(self) -> None:
        self._tips: list[tuple[float, float]] = []
        self._targets: list[tuple[float, float]] = []
        self._matrix: np.ndarray | None = None

    def add(self, tip_norm: tuple[float, float], target_norm: tuple[float, float]) -> None:
        self._tips.append(tip_norm)
        self._targets.append(target_norm)
        self._matrix = None

    @property
    def ready(self) -> bool:
        return len(self._tips) >= 4

    def map(self, tip_norm: tuple[float, float]) -> tuple[float, float] | None:
        if not self.ready:
            return None
        if self._matrix is None:
            a = np.array([[x, y, 1.0] for x, y in self._tips])
            self._matrix, *_ = np.linalg.lstsq(a, np.array(self._targets), rcond=None)
        x, y = np.array([tip_norm[0], tip_norm[1], 1.0]) @ self._matrix
        return min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)


def run_demo(camera_index: int = 0, seconds: float = 30.0) -> int:
    """Show the camera with the detected pen drawn on it, then print how well it did.

    Hold a pen like a wand in view of the camera. Keep it still for a few
    seconds at some point: the jitter figure comes from the steadiest second.
    """
    import time

    import cv2

    from superhero.perception.hands import HandTracker, download_hand_model

    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        print("Cannot open the camera (is SUPERHERO itself still running?).")
        return 3
    tracker = HandTracker(download_hand_model())
    frames = with_hand = with_pen = 0
    tips: list[tuple[float, float, float]] = []
    start = time.monotonic()
    try:
        while time.monotonic() - start < seconds:
            ok, frame = capture.read()
            if not ok:
                continue
            frame = cv2.flip(frame, 1)
            frames += 1
            hand = tracker.process(frame, int((time.monotonic() - start) * 1000))
            if hand is not None:
                with_hand += 1
                pen = detect_pen(frame, hand)
                if pen is not None:
                    with_pen += 1
                    tips.append((time.monotonic(), *pen.tip))
                    tip, tail = (tuple(map(int, p)) for p in (pen.tip, pen.tail))
                    cv2.line(frame, tail, tip, (0, 255, 255), 2)
                    cv2.circle(frame, tip, 7, (0, 0, 255), -1)
            label = f"hand {with_hand}/{frames}  pen {with_pen}/{max(with_hand, 1)}  (Esc to stop)"
            cv2.putText(frame, label, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imshow("SUPERHERO pen proof of concept", frame)
            if cv2.waitKey(1) == 27:
                break
    finally:
        capture.release()
        tracker.close()
        cv2.destroyAllWindows()

    print(f"frames: {frames}   hand found: {with_hand}   pen found: {with_pen}")
    if with_hand:
        print(f"pen detection rate while a hand was visible: {100 * with_pen / with_hand:.0f}%")
    jitter = steadiest_jitter(tips)
    if jitter is not None:
        print(f"tip jitter in the steadiest second: {jitter:.1f} px (standard deviation)")
    return 0


def steadiest_jitter(
    tips: Sequence[tuple[float, float, float]], window_s: float = 1.0
) -> float | None:
    """Smallest positional spread of the tip over any one-second window with enough samples."""
    best: float | None = None
    for i, (t0, _x, _y) in enumerate(tips):
        window = [(x, y) for t, x, y in tips[i:] if t - t0 <= window_s]
        if len(window) < 8:
            continue
        spread = math.hypot(*np.std(np.array(window), axis=0))
        best = spread if best is None else min(best, spread)
    return best
