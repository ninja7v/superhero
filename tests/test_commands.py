import pytest

from superhero.interaction.commands import Command, builtin_commands, parse, parse_spec


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Copy.", Command("key", "ctrl+c")),
        ("Paste", Command("key", "ctrl+v")),
        ("Undo!", Command("key", "ctrl+z")),
        ("Close this window.", Command("key", "alt+f4")),
        ("Minimize this window", Command("key", "super+h")),
        ("please maximize the window", Command("key", "super+up")),
        ("Scroll down.", Command("scroll", "down")),
        ("Right click", Command("action", "right_click")),
        ("Stop listening", Command("stop", "")),
        ("Switch to Firefox.", Command("launch", "firefox")),
        ("Open my development folder", Command("launch", "development folder")),
    ],
)
def test_spoken_phrases_on_linux(spoken, expected):
    assert parse(spoken, platform="linux") == expected


def test_ordinary_sentences_are_not_commands():
    for sentence in ("I will copy the report tomorrow", "Hello there", "", "..."):
        assert parse(sentence, platform="linux") is None
    # A long "open ..." sentence is dictation, not an app launch.
    assert parse("open the door and walk into the garden slowly", platform="linux") is None


def test_platform_specific_shortcuts():
    assert parse("copy", platform="darwin") == Command("key", "cmd+c")
    assert parse("close window", platform="darwin") == Command("key", "cmd+w")
    assert parse("minimize", platform="win32") == Command("key", "super+down")
    assert parse("redo", platform="win32") == Command("key", "ctrl+y")
    assert parse("maximize", platform="darwin").kind == "unsupported"


def test_close_all_windows_is_refused_not_dictated():
    assert parse("Close all windows", platform="linux").kind == "unsupported"


def test_custom_commands_extend_and_override():
    custom = {
        "Open terminal": "key:ctrl+alt+t",
        "copy": "type:copied!",
        "sign off": "type:Kind regards, L.",
    }
    assert parse("open terminal", custom, "linux") == Command("key", "ctrl+alt+t")
    assert parse("Copy", custom, "linux") == Command("type", "copied!")
    assert parse("sign off", custom, "linux") == Command("type", "Kind regards, L.")


def test_window_commands_never_force_quit():
    """Closing goes through the desktop's own close shortcut, so save prompts still appear."""
    for platform in ("linux", "win32", "darwin"):
        specs = " ".join(builtin_commands(platform).values())
        assert "kill" not in specs and "cmd+q" not in specs


def test_parse_spec_keeps_colons_in_values():
    assert parse_spec("type:Re: hello") == Command("type", "Re: hello")


# -- spec validation: a typo is reported, never pressed --------------------------------


def test_chords_are_normalised_and_unknown_keys_rejected():
    from superhero.interaction.commands import parse_chord

    assert parse_chord("Ctrl + Shift + T") == "ctrl+shift+t"
    assert parse_chord("win+e") == parse_chord("meta+e") == "super+e"
    assert parse_chord("Control+Esc") == "ctrl+escape"
    for bad in ("ctrl+alt+tt", "", "ctrl+", "hyper+a"):
        with pytest.raises(ValueError):
            parse_chord(bad)


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_every_builtin_command_is_valid(platform):
    for phrase, spec in builtin_commands(platform).items():
        assert parse_spec(spec).kind != "invalid", (phrase, spec)


@pytest.mark.parametrize(
    ("spec", "problem"),
    [
        ("key:ctrl+alt+tt", "unknown key 'tt'"),
        ("action:explode", "unknown action"),
        ("action:none", "unknown action"),
        ("scroll:sideways", "up or down"),
        ("type:", "needs a value"),
        ("launch", "needs a value"),
        ("press:ctrl+c", "unknown kind 'press'"),
    ],
)
def test_malformed_custom_specs_become_invalid_commands(spec, problem):
    from superhero.interaction.commands import spec_problem

    command = parse("do it", {"do it": spec}, "linux")
    assert command.kind == "invalid" and problem in command.value
    assert problem in spec_problem(spec)


def test_valid_custom_specs_have_no_problem():
    from superhero.interaction.commands import spec_problem

    for spec in ("key:Ctrl+Alt+T", "stop", "Stop", "type:Kind regards", "action:left_click"):
        assert spec_problem(spec) == "", spec
    assert parse("hush", {"hush": "stop"}, "linux") == Command("stop", "")
    assert parse("term", {"term": "key:Ctrl + Alt + T"}, "linux") == Command("key", "ctrl+alt+t")
