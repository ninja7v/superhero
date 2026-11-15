import pytest

pytest.importorskip("jeepney")  # the portal adapter is Linux-only

from superhero.platform.displays import Display
from superhero.platform.portal import PortalInput, keysym


def test_keysyms():
    assert keysym("a") == 0x61 and keysym("A") == 0x41
    assert keysym("é") == 0xE9  # Latin-1 maps directly
    assert keysym("€") == 0x01000000 + 0x20AC  # Unicode range
    assert keysym("\n") == keysym("enter") == 0xFF0D
    assert keysym("f4") == 0xFFC1 and keysym("f12") == 0xFFC9
    assert keysym("super") == keysym("cmd")
    with pytest.raises(ValueError):
        keysym("hyper")


def test_streams_are_matched_to_displays_by_geometry():
    left = Display("left", -1280, 0, 1280, 1024)
    main = Display("main", 0, 0, 1920, 1080, primary=True)
    streams = [
        (41, {"position": ("(ii)", (0, 0)), "size": ("(ii)", (1920, 1080))}),
        (57, {"position": ("(ii)", (-1280, 0)), "size": ("(ii)", (1280, 1024))}),
    ]
    assert PortalInput._match_streams(streams, [left, main]) == {
        "main": (41, 1920, 1080),
        "left": (57, 1280, 1024),
    }


def test_only_shared_displays_are_matched():
    main = Display("main", 0, 0, 1920, 1080)
    other = Display("other", 1920, 0, 2560, 1440)
    streams = [(9, {"position": ("(ii)", (1920, 0)), "size": ("(ii)", (2560, 1440))})]
    assert PortalInput._match_streams(streams, [main, other]) == {"other": (9, 2560, 1440)}


def test_stream_without_geometry_falls_back_to_first_display():
    main = Display("main", 0, 0, 1920, 1080)
    assert PortalInput._match_streams([(3, {})], [main]) == {"main": (3, 1920, 1080)}


def test_scaled_geometry_falls_back_to_pairing_by_position():
    """Under fractional scaling the portal's logical sizes differ from XWayland's."""
    left = Display("left", 0, 0, 2560, 1440)
    right = Display("right", 2560, 0, 1920, 1080)
    streams = [  # the same two monitors at 1.25x, as the compositor reports them
        (8, {"position": ("(ii)", (2048, 0)), "size": ("(ii)", (1536, 864))}),
        (7, {"position": ("(ii)", (0, 0)), "size": ("(ii)", (2048, 1152))}),
    ]
    assert PortalInput._match_streams(streams, [left, right]) == {
        "left": (7, 2048, 1152),
        "right": (8, 1536, 864),
    }


def test_ambiguous_leftovers_are_not_guessed():
    main = Display("main", 0, 0, 1920, 1080)
    other = Display("other", 1920, 0, 1920, 1080)
    streams = [(5, {"position": ("(ii)", (0, 0)), "size": ("(ii)", (1536, 864))})]
    assert PortalInput._match_streams(streams, [main, other]) == {}
