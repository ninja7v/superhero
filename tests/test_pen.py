from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from superhero.perception.pen import TipCalibration, detect_pen, steadiest_jitter

W, H = 640, 480


def hand_at(wrist, grip, size=60):
    """Minimal hand: wrist, middle knuckle one hand-size toward the grip, thumb/index tips at the grip."""
    wrist, grip = np.array(wrist, float), np.array(grip, float)
    direction = (grip - wrist) / np.linalg.norm(grip - wrist)
    pts = [wrist] * 21
    pts[9] = wrist + direction * size
    pts[4] = pts[8] = grip
    return [SimpleNamespace(x=p[0] / W, y=p[1] / H, z=0.0) for p in pts]


def scene(*segments, noise=6):
    rng = np.random.default_rng(0)
    image = np.full((H, W, 3), 170, np.uint8)
    image = np.clip(image + rng.normal(0, noise, image.shape), 0, 255).astype(np.uint8)
    for a, b, thickness in segments:
        cv2.line(image, a, b, (30, 30, 30), thickness, cv2.LINE_AA)
    return image


def test_finds_the_tip_of_a_pen_held_in_the_hand():
    # Hand at the lower centre, pen running up and to the right through the grip.
    hand = hand_at(wrist=(300, 400), grip=(330, 310))
    image = scene(((315, 355), (390, 130), 6))
    pen = detect_pen(image, hand)
    assert pen is not None
    assert pen.tip == pytest.approx((390, 130), abs=10)
    assert pen.length_ratio > 2


@pytest.mark.parametrize("angle", [0, 45, 90, 135, 180])
def test_any_orientation(angle):
    wrist = np.array([320.0, 280.0])
    d = np.array([np.cos(np.radians(angle)), -np.sin(np.radians(angle))])
    grip = wrist + d * 90
    tip = wrist + d * 230
    image = scene((tuple(map(int, wrist + d * 60)), tuple(map(int, tip)), 6))
    pen = detect_pen(image, hand_at(tuple(wrist), tuple(grip)))
    assert pen is not None and pen.tip == pytest.approx(tuple(tip), abs=12)


def test_empty_hand_gives_nothing():
    assert detect_pen(scene(), hand_at((300, 400), (330, 310))) is None


def test_forearm_edge_is_not_a_pen():
    # A long straight edge through the wrist but pointing away from the fingers.
    hand = hand_at(wrist=(300, 300), grip=(300, 210))
    assert detect_pen(scene(((300, 300), (300, 470), 8)), hand) is None


def test_line_far_from_the_grip_is_ignored():
    hand = hand_at(wrist=(300, 400), grip=(330, 310))
    assert detect_pen(scene(((500, 50), (600, 300), 6)), hand) is None


def test_degenerate_hands_are_ignored():
    assert detect_pen(scene(), hand_at((300, 400), (330, 310))[:5]) is None
    assert detect_pen(scene(), hand_at((300, 400), (330, 310), size=2)) is None


def test_tip_calibration_recovers_an_affine_map_including_a_mirror():
    truth = lambda x, y: (1.0 - (x - 0.2) / 0.6, (y - 0.1) / 0.7)  # noqa: E731
    calibration = TipCalibration()
    assert calibration.map((0.5, 0.5)) is None
    for tip in [(0.2, 0.1), (0.8, 0.1), (0.2, 0.8), (0.8, 0.8), (0.5, 0.45)]:
        calibration.add(tip, truth(*tip))
    assert calibration.map((0.35, 0.6)) == pytest.approx(truth(0.35, 0.6), abs=1e-6)
    assert calibration.map((5.0, 5.0)) == (0.0, 1.0)  # clamped onto the display


def test_steadiest_jitter_picks_the_calm_second():
    moving = [(i * 0.05, 100 + i * 20.0, 100.0) for i in range(20)]
    still = [(1.0 + i * 0.05, 400 + (i % 2), 200.0) for i in range(30)]
    assert steadiest_jitter(moving + still) < 1.0
    assert steadiest_jitter(moving[:3]) is None
