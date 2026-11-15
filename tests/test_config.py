import json

import pytest

from superhero import config as cfg


def test_round_trip_preserves_changes():
    c = cfg.Config()
    c.camera.index = 2
    c.gestures.bindings["long_blink"] = "left_click"
    c.voice.custom_commands["open terminal"] = "key:ctrl+alt+t"
    cfg.save(c, "work")
    loaded = cfg.load("work")
    assert loaded.to_dict() == c.to_dict()
    assert "work" in cfg.list_profiles()


def test_missing_file_gives_defaults():
    assert cfg.load("nope").to_dict() == cfg.Config().to_dict()


def test_unknown_keys_and_wrong_types_are_ignored():
    path = cfg.profile_path("odd")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "future_setting": 1,
                "camera": {"index": "zero", "fps": 60, "mirror": 1},
                "pointer": {"size": 20.7, "color": 5},
                "gestures": {"bindings": {"dwell": "left_click"}},
            }
        )
    )
    c = cfg.load("odd")
    assert c.camera.index == 0  # a string is not an index
    assert c.camera.fps == 60
    assert c.camera.mirror is True  # 1 is not a bool
    assert c.pointer.size == 20 and c.pointer.color == "auto"
    assert c.gestures.bindings["dwell"] == "left_click"
    assert c.gestures.bindings["wink_left"] == "left_click"  # other defaults survive


def test_corrupt_file_gives_defaults():
    path = cfg.profile_path("bad")
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert cfg.load("bad").camera.fps == 30


@pytest.mark.parametrize("name", ["", "../escape", "a/b"])
def test_profile_names_cannot_escape_the_folder(name):
    with pytest.raises(ValueError):
        cfg.profile_path(name)


def test_calibration_is_per_display_and_camera():
    a = cfg.calibration_path("HDMI-1", 0)
    assert a != cfg.calibration_path("HDMI-1", 1) != cfg.calibration_path("DP-2", 1)
    assert cfg.calibration_path("../../x y", 0).parent == a.parent


def test_every_default_binding_uses_known_names():
    bindings = cfg.Config().gestures.bindings
    assert set(bindings) == set(cfg.GESTURES)
    assert set(bindings.values()) <= set(cfg.ACTIONS)


# -- hand-edited and imported profiles are checked on load -------------------------------


def write_profile(name, data):
    path = cfg.profile_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def test_out_of_range_values_are_clamped_to_what_the_panel_allows():
    write_profile(
        "wild",
        {
            "camera": {"fps": 0, "width": 99999},
            "pointer": {"size": -5, "min_confidence": 7.0},
            "gestures": {"cooldown_ms": 1e9, "mouth_open_threshold": float("nan")},
        },
    )
    c = cfg.load("wild")
    assert c.camera.fps == 10  # 0 would stop the perception thread (Fovea's frame gate)
    assert c.camera.width == 3840
    assert c.pointer.size == 3 and c.pointer.min_confidence == 1.0
    assert c.gestures.cooldown_ms == 3000
    assert c.gestures.mouth_open_threshold == cfg.GestureConfig().mouth_open_threshold


def test_invalid_choices_keep_their_defaults():
    write_profile(
        "choices",
        {
            "calibration_points": 7,
            "pointer": {"style": "laser-beam", "color": "#zzzzzz"},
            "voice": {"mode": "telepathy", "model": "  "},
            "gestures": {"bindings": {"wink_left": "explode", "nonsense": "left_click"}},
        },
    )
    c = cfg.load("choices")
    defaults = cfg.Config()
    assert c.calibration_points == defaults.calibration_points
    assert c.pointer.style == "dot" and c.pointer.color == "auto"
    assert c.voice.mode == "auto" and c.voice.model == "base"
    assert c.gestures.bindings == defaults.gestures.bindings


def test_valid_choices_are_kept():
    write_profile("fine", {"calibration_points": 13, "pointer": {"color": "#00FF7f"}})
    c = cfg.load("fine")
    assert c.calibration_points == 13 and c.pointer.color == "#00FF7f"


def test_every_panel_range_contains_its_default():
    defaults = {type(section): section for section in vars(cfg.Config()).values()}
    for kind, limits in cfg.RANGES.items():
        for name, (lo, hi) in limits.items():
            assert lo <= getattr(defaults[kind], name) <= hi, (kind.__name__, name)


def test_an_unreadable_profile_is_kept_aside_not_overwritten():
    path = write_profile("broken", "{ this is not json")
    assert cfg.load("broken").to_dict() == cfg.Config().to_dict()
    assert not path.exists()
    assert path.with_name("broken.json.bad").read_text() == "{ this is not json"


def test_read_file_is_strict(tmp_path):
    for content in ("[1, 2]", "not json", '"text"'):
        bad = tmp_path / "bad.json"
        bad.write_text(content)
        with pytest.raises(ValueError):
            cfg.read_file(bad)
    with pytest.raises(OSError):
        cfg.read_file(tmp_path / "missing.json")
