from types import SimpleNamespace

import pytest

from superhero.perception.hands import HandGestureDetector, classify_pose

MS = 1_000_000
BASE_X = {"index": 0.46, "middle": 0.50, "ring": 0.54, "pinky": 0.58}
FIRST = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}


def hand(extended=(), thumb_tip=(0.36, 0.62), index_tip=None, scale=1.0, rotate=False):
    """A synthetic upright hand: wrist at the bottom, knuckles above it."""
    pts = [(0.5, 0.8)] * 21
    pts[1:5] = [(0.45, 0.76), (0.41, 0.72), (0.38, 0.67), thumb_tip]
    for name, x in BASE_X.items():
        i = FIRST[name]
        if name in extended:
            pts[i : i + 4] = [(x, 0.60), (x, 0.52), (x, 0.46), (x, 0.40)]
        else:  # folded back toward the palm
            pts[i : i + 4] = [(x, 0.60), (x, 0.52), (x, 0.56), (x, 0.62)]
    if index_tip is not None:
        pts[8] = index_tip
    wx, wy = pts[0]
    out = []
    for x, y in pts:
        dx, dy = (x - wx) * scale, (y - wy) * scale
        if rotate:  # 90 degrees: the hand points sideways
            dx, dy = -dy, dx
        out.append(SimpleNamespace(x=wx + dx, y=wy + dy, z=0.0))
    return out


ALL = ("index", "middle", "ring", "pinky")
PINCH = dict(extended=("middle", "ring", "pinky"), thumb_tip=(0.42, 0.57), index_tip=(0.42, 0.56))


@pytest.mark.parametrize(
    ("pose", "expected"),
    [
        (hand(extended=ALL), "hand_open_palm"),
        (hand(), "hand_fist"),
        (hand(extended=("index", "middle")), "hand_two_fingers"),
        (hand(**PINCH), "hand_pinch"),
        (hand(extended=("index",)), None),  # pointing is not bound to anything
        (hand(extended=("index", "middle", "ring")), None),
    ],
)
def test_poses(pose, expected):
    assert classify_pose(pose) == expected


def test_fist_is_not_mistaken_for_a_pinch():
    # In a fist the thumb rests on the index finger, which looks like a pinch up close.
    assert classify_pose(hand(thumb_tip=(0.46, 0.61))) == "hand_fist"


def test_open_hand_with_thumb_alongside_is_a_palm_not_a_pinch():
    # Measured on a real photo: a relaxed open hand has a thumb-to-index gap of ~0.35 hand sizes.
    assert classify_pose(hand(extended=ALL, thumb_tip=(0.44, 0.47))) == "hand_open_palm"


def test_poses_do_not_depend_on_size_or_rotation():
    for kwargs in (dict(scale=0.4), dict(scale=1.6), dict(rotate=True)):
        assert classify_pose(hand(extended=ALL, **kwargs)) == "hand_open_palm"
        assert classify_pose(hand(**kwargs)) == "hand_fist"
        assert classify_pose(hand(**PINCH, **kwargs)) == "hand_pinch"


def test_incomplete_landmarks_are_ignored():
    assert classify_pose(hand()[:10]) is None
    assert classify_pose([SimpleNamespace(x=0.5, y=0.5, z=0)] * 21) is None


def feed(detector, frames, step_ms=33):
    return [g.kind for i, f in enumerate(frames) if (g := detector.update(f, i * step_ms * MS))]


def test_pose_must_be_held_and_fires_once():
    detector = HandGestureDetector(hold_ms=400)
    assert feed(detector, [hand(extended=ALL)] * 8) == []  # 230 ms: too short
    assert feed(HandGestureDetector(400), [hand(extended=ALL)] * 60) == ["hand_open_palm"]


def test_pinch_fires_quickly_and_again_after_release():
    detector = HandGestureDetector(hold_ms=400)
    frames = (
        [hand(extended=ALL)] * 3
        + [hand(**PINCH)] * 5
        + [hand(extended=ALL)] * 3
        + [hand(**PINCH)] * 5
    )
    assert feed(detector, frames) == ["hand_pinch", "hand_pinch"]


def test_a_single_noisy_frame_does_not_pinch():
    detector = HandGestureDetector(hold_ms=400)
    frames = [hand(extended=ALL)] * 3 + [hand(**PINCH)] + [hand(extended=ALL)] * 3
    assert feed(detector, frames) == []


def test_hand_leaving_the_frame_resets():
    detector = HandGestureDetector(hold_ms=400)
    frames = [hand()] * 8 + [None] * 2 + [hand()] * 8  # two short holds, never 400 ms
    assert feed(detector, frames) == []


def test_a_failed_download_leaves_no_partial_file(tmp_path, monkeypatch):
    from superhero.perception import hands as hands_module

    monkeypatch.setattr(hands_module, "user_cache_dir", lambda _name: str(tmp_path))
    seen = {}

    def stalled(url, timeout):
        seen["timeout"] = timeout
        raise TimeoutError("timed out")

    monkeypatch.setattr(hands_module.urllib.request, "urlopen", stalled)
    with pytest.raises(TimeoutError):
        hands_module.download_hand_model()
    assert seen["timeout"] > 0  # a stalled network can never hang the caller
    assert list((tmp_path / "models").iterdir()) == []
