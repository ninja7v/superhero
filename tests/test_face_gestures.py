from fovea.events import GesturePhase

from superhero.perception.face_gestures import HeadGestureDetector, MouthOpenDetector

MS = 1_000_000


def feed_mouth(detector, values, step_ms=33):
    return [g for i, v in enumerate(values) if (g := detector.update(v, i * step_ms * MS))]


def test_sustained_open_mouth_fires_exactly_once():
    out = feed_mouth(MouthOpenDetector(0.45, 1000), [0.05] * 5 + [0.7] * 90)
    assert [g.phase for g in out] == [GesturePhase.STARTED, GesturePhase.ENDED]
    assert out[1].timestamp_ns - out[0].timestamp_ns >= 1000 * MS


def test_brief_opening_is_cancelled_not_fired():
    out = feed_mouth(MouthOpenDetector(0.45, 1000), [0.7] * 10 + [0.05] * 5)
    assert [g.phase for g in out] == [GesturePhase.STARTED, GesturePhase.CANCELLED]


def test_talking_never_fires():
    # Jaw opening and closing ~3 times a second for 6 seconds.
    talking = ([0.6] * 5 + [0.1] * 5) * 18
    out = feed_mouth(MouthOpenDetector(0.45, 1000), talking)
    assert GesturePhase.ENDED not in [g.phase for g in out]


def test_mouth_must_close_before_firing_again():
    detector = MouthOpenDetector(0.45, 500)
    held = feed_mouth(detector, [0.7] * 200)
    assert [g.phase for g in held].count(GesturePhase.ENDED) == 1
    again = [
        g
        for i, v in enumerate([0.05] * 3 + [0.7] * 40)
        if (g := detector.update(v, (300 + i) * 33 * MS))
    ]
    assert [g.phase for g in again] == [GesturePhase.STARTED, GesturePhase.ENDED]


def test_hysteresis_band_does_not_cancel():
    # Dips to 0.35 (between 0.27 and 0.45) keep the hold alive.
    out = feed_mouth(MouthOpenDetector(0.45, 1000), [0.7, 0.35] * 30)
    assert [g.phase for g in out] == [GesturePhase.STARTED, GesturePhase.ENDED]


def feed_head(detector, poses, step_ms=33):
    out = []
    for i, (pitch, roll) in enumerate(poses):
        out += [g.kind for g in detector.update(pitch, roll, i * step_ms * MS)]
    return out


def test_held_tilt_repeats_for_scrolling():
    detector = HeadGestureDetector(tilt_deg=14, nod_deg=10, roll_sign=-1)
    out = feed_head(detector, [(0, 0)] * 10 + [(0, -20)] * 30)  # ~1 s tilted, mirrored
    assert set(out) == {"head_tilt_right"}
    assert 3 <= len(out) <= 5  # every 250 ms


def test_tilt_direction_follows_mirroring():
    mirrored = HeadGestureDetector(14, 10, roll_sign=-1)
    plain = HeadGestureDetector(14, 10, roll_sign=1)
    poses = [(0, 0)] * 5 + [(0, 20)] * 3
    assert feed_head(mirrored, poses)[0] == "head_tilt_left"
    assert feed_head(plain, poses)[0] == "head_tilt_right"


def test_quick_nod_fires_and_slow_look_down_does_not():
    nod = [(0, 0)] * 10 + [(14, 0)] * 8 + [(0, 0)] * 5
    assert feed_head(HeadGestureDetector(14, 10), nod) == ["head_nod"]
    reading = [(0, 0)] * 10 + [(14, 0)] * 60 + [(0, 0)] * 5  # looking down for 2 s
    assert feed_head(HeadGestureDetector(14, 10), reading) == []


def test_small_movements_are_ignored_and_baseline_adapts():
    detector = HeadGestureDetector(14, 10)
    # Drift slowly to a new resting posture, then wobble a little around it.
    drift = [(i * 0.02, i * 0.02) for i in range(600)]
    wobble = [(12 + (i % 3), 12 + (i % 3)) for i in range(60)]
    assert feed_head(detector, drift + wobble) == []
