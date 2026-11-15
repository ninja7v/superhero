"""Deliberate mouth and head gestures from face landmarks.

Both detectors emit Fovea ``Gesture`` events. A gesture *fires* on
``GesturePhase.ENDED``; ``STARTED`` and ``CANCELLED`` bracket a candidate so
consumers can prepare (the voice recorder arms its pre-buffer on ``STARTED``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fovea.events import Gesture, GesturePhase

_NS_PER_MS = 1_000_000


@dataclass
class MouthOpenDetector:
    """Fire once when the jaw stays open past ``threshold`` for ``hold_ms``.

    Speech opens and closes the jaw several times a second, so requiring an
    unbroken hold is what separates the gesture from talking. Yawns are the
    known false positive: they are sustained too. The mouth must close (below
    60% of the threshold) before the gesture can fire again.
    """

    threshold: float
    hold_ms: float
    _since_ns: int | None = field(default=None, init=False)
    _fired: bool = field(default=False, init=False)

    def update(self, jaw_open: float, timestamp_ns: int) -> Gesture | None:
        if jaw_open < self.threshold * 0.6:
            cancelled = self._since_ns is not None and not self._fired
            self.reset()
            return (
                _gesture("mouth_open", GesturePhase.CANCELLED, timestamp_ns) if cancelled else None
            )
        if jaw_open < self.threshold or self._fired:
            return None  # inside the hysteresis band, or waiting for the mouth to close
        if self._since_ns is None:
            self._since_ns = timestamp_ns
            return _gesture("mouth_open", GesturePhase.STARTED, timestamp_ns)
        if timestamp_ns - self._since_ns >= self.hold_ms * _NS_PER_MS:
            self._fired = True
            return _gesture("mouth_open", GesturePhase.ENDED, timestamp_ns, min(1.0, jaw_open))
        return None

    def reset(self) -> None:
        self._since_ns = None
        self._fired = False


@dataclass
class HeadGestureDetector:
    """Head tilt (held, repeating) and nod (down and back) relative to a resting pose.

    The resting pose is learned as a slow average while the head is near it, so
    the detector follows gradual posture changes without a setup step.
    ``roll_sign`` is -1 when the camera image is mirrored: Fovea reports
    clockwise-in-image roll as positive, and a mirrored tilt toward the user's
    left shoulder is counter-clockwise.
    """

    tilt_deg: float
    nod_deg: float
    roll_sign: float = -1.0
    repeat_ms: float = 250.0
    nod_max_ms: float = 700.0
    _base_pitch: float | None = field(default=None, init=False)
    _base_roll: float | None = field(default=None, init=False)
    _last_tilt_ns: int | None = field(default=None, init=False)
    _nod_since_ns: int | None = field(default=None, init=False)

    def update(self, pitch_deg: float, roll_deg: float, timestamp_ns: int) -> list[Gesture]:
        if self._base_pitch is None or self._base_roll is None:
            self._base_pitch, self._base_roll = pitch_deg, roll_deg
            return []
        d_pitch = pitch_deg - self._base_pitch
        d_roll = (roll_deg - self._base_roll) * self.roll_sign
        if abs(d_pitch) < self.nod_deg / 2 and abs(d_roll) < self.tilt_deg / 2:
            self._base_pitch += 0.01 * (pitch_deg - self._base_pitch)
            self._base_roll += 0.01 * (roll_deg - self._base_roll)

        gestures: list[Gesture] = []
        if abs(d_roll) >= self.tilt_deg:
            due = self._last_tilt_ns is None or (
                timestamp_ns - self._last_tilt_ns >= self.repeat_ms * _NS_PER_MS
            )
            if due:
                self._last_tilt_ns = timestamp_ns
                name = "head_tilt_right" if d_roll > 0 else "head_tilt_left"
                gestures.append(_gesture(name, GesturePhase.ENDED, timestamp_ns))
        else:
            self._last_tilt_ns = None

        if d_pitch >= self.nod_deg:  # positive pitch is nodding down
            if self._nod_since_ns is None:
                self._nod_since_ns = timestamp_ns
        elif d_pitch < self.nod_deg / 2 and self._nod_since_ns is not None:
            quick = timestamp_ns - self._nod_since_ns <= self.nod_max_ms * _NS_PER_MS
            self._nod_since_ns = None
            if quick:
                gestures.append(_gesture("head_nod", GesturePhase.ENDED, timestamp_ns))
        return gestures

    def reset(self) -> None:
        self._base_pitch = self._base_roll = None
        self._last_tilt_ns = self._nod_since_ns = None


def _gesture(kind: str, phase: GesturePhase, timestamp_ns: int, confidence: float = 1.0) -> Gesture:
    return Gesture(kind=kind, phase=phase, confidence=confidence, timestamp_ns=timestamp_ns)
