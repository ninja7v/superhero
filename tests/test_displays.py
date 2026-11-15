import pytest

from superhero.platform.displays import Display, pick_display

MAIN = Display("main", 0, 0, 1920, 1080, primary=True, width_mm=600, height_mm=340)
LEFT = Display("left", -1280, 56, 1280, 1024)  # left of the primary: negative origin
HIDPI = Display("hidpi", 1920, 0, 1280, 800, scale=2.0)
TALL = Display("tall", 3200, -400, 1080, 1920)


def test_corners_map_to_the_display_edges():
    assert MAIN.logical_point(0, 0) == (0, 0)
    assert MAIN.logical_point(1, 1) == (1919, 1079)
    assert MAIN.logical_point(0.5, 0.5) == (960, 540)


def test_out_of_range_gaze_is_clamped_onto_the_display():
    assert MAIN.logical_point(-0.3, 1.7) == (0, 1079)


def test_negative_desktop_coordinates():
    assert LEFT.logical_point(0, 0) == (-1280, 56)
    assert LEFT.logical_point(1, 1) == (-1, 1079)


def test_portrait_display():
    assert TALL.portrait and not MAIN.portrait
    assert TALL.logical_point(1, 1) == (3200 + 1079, -400 + 1919)


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", (1920 + 640, 400)),  # points == logical
        ("win32", (1920 + 1279, 799)),  # native origin, scaled extent
        ("linux", ((1920 + 640) * 2, 800)),  # whole desktop scaled
    ],
)
def test_native_point_per_platform_on_a_scaled_display(platform, expected):
    assert HIDPI.native_point(0.5, 0.5, platform) == pytest.approx(expected, abs=2)


def test_unscaled_native_equals_logical_everywhere():
    for platform in ("darwin", "win32", "linux"):
        assert LEFT.native_point(0.25, 0.75, platform) == LEFT.logical_point(0.25, 0.75)


def test_error_in_degrees_needs_physical_size():
    assert LEFT.error_to_degrees(0.05) is None
    assert 1.5 < MAIN.error_to_degrees(0.05) < 3.5  # ~24 mm at 60 cm


def test_pick_display_falls_back_to_primary():
    displays = [LEFT, MAIN, HIDPI]
    assert pick_display(displays, "hidpi") is HIDPI
    assert pick_display(displays, "unplugged") is MAIN
    assert pick_display([LEFT], "") is LEFT
    with pytest.raises(ValueError):
        pick_display([], "")
