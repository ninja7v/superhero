import pytest
from fovea.events import (
    DoubleBlink,
    Eye,
    Fixation,
    GazePoint,
    Gesture,
    GesturePhase,
    TrackingState,
    TrackingStatus,
    Wink,
)

from superhero.config import Config
from superhero.interaction.commands import Command
from superhero.interaction.engine import InteractionEngine
from superhero.platform.displays import Display

DISPLAY = Display("main", 0, 0, 1920, 1080, primary=True)


class FakeInput:
    name = "fake"

    def __init__(self):
        self.calls = []
        self.fail = False

    def _record(self, *call):
        if self.fail:
            raise RuntimeError("session closed")
        self.calls.append(call)

    def move(self, display, nx, ny):
        self._record("move", *display.logical_point(nx, ny))

    def button(self, button, pressed):
        self._record("button", button, pressed)

    def scroll(self, lines):
        self._record("scroll", lines)

    def keys(self, combo):
        self._record("keys", "+".join(combo))

    def type_text(self, text):
        self._record("type", text)

    def close(self):
        pass


class Rig:
    def __init__(self, armed=True, **kwargs):
        self.now = 100.0
        self.config = Config()
        self.input = FakeInput()
        self.voice_toggles = 0
        self.voice_arms = []
        self.engine = InteractionEngine(
            self.config,
            DISPLAY,
            self.input,
            on_voice_toggle=lambda: setattr(self, "voice_toggles", self.voice_toggles + 1),
            on_voice_arm=self.voice_arms.append,
            clock=lambda: self.now,
            **{"sleep": lambda _s: None, **kwargs},
        )
        self.engine.set_armed(armed)
        self.engine.handle(TrackingState(TrackingStatus.ACTIVE, 0.9, 0))

    def look(self, x=0.5, y=0.5, confidence=0.9):
        self.engine.handle(GazePoint(x, y, confidence, 0))

    def wink(self, eye=Eye.RIGHT):
        self.engine.handle(Wink(eye, 200.0, 0.9, 0))


def test_wink_clicks_where_the_user_is_looking():
    rig = Rig()
    rig.look(0.25, 0.75)
    rig.wink()
    assert rig.input.calls == [
        ("move", 480, 809),
        ("button", "left", True),
        ("button", "left", False),
    ]


def test_nothing_is_injected_while_disarmed():
    rig = Rig(armed=False)
    rig.look()
    rig.wink()
    rig.engine.handle(DoubleBlink(0))
    assert not rig.engine.run_command(Command("key", "ctrl+c"))
    assert not rig.engine.dictate("hello")
    assert rig.input.calls == []


def test_looking_alone_never_moves_the_system_cursor_by_default():
    rig = Rig()
    rig.look(0.1, 0.1)
    rig.look(0.9, 0.9)
    assert rig.input.calls == []
    rig.config.pointer.move_system_cursor = True
    rig.look(0.5, 0.5)
    assert rig.input.calls == [("move", 960, 540)]


def test_mirrored_camera_swaps_wink_sides():
    rig = Rig()
    rig.look()
    rig.wink(Eye.LEFT)  # topology-left on a mirrored frame is the user's right eye
    assert ("button", "right", True) in rig.input.calls
    plain = Rig()
    plain.config.camera.mirror = False
    plain.look()
    plain.wink(Eye.LEFT)
    assert ("button", "left", True) in plain.input.calls


def test_click_is_refused_on_stale_or_missing_gaze():
    rig = Rig()
    rig.wink()  # never looked anywhere
    rig.look()
    rig.now += 2.0  # gaze is two seconds old
    rig.wink()
    assert rig.input.calls == []


def test_click_is_refused_when_tracking_is_lost():
    rig = Rig()
    rig.look()
    rig.engine.handle(TrackingState(TrackingStatus.LOST, 0.0, 0))
    rig.wink()
    assert rig.input.calls == []
    assert not rig.engine.pointer_visible


def test_low_confidence_gaze_hides_pointer_and_keeps_last_good_position():
    rig = Rig()
    rig.look(0.3, 0.3)
    rig.look(0.9, 0.9, confidence=0.1)
    assert rig.engine.pointer == (0.3, 0.3)
    assert not rig.engine.pointer_visible


def test_cooldown_blocks_rapid_repeats():
    rig = Rig()
    rig.look()
    rig.wink()
    rig.now += 0.1
    rig.look()
    rig.wink()
    assert len(rig.input.calls) == 3
    rig.now += 0.5
    rig.look()
    rig.wink()
    assert len(rig.input.calls) == 6


def test_double_blink_double_clicks():
    rig = Rig()
    rig.look()
    rig.engine.handle(DoubleBlink(0))
    assert [c for c in rig.input.calls if c[0] == "button"] == [
        ("button", "left", True),
        ("button", "left", False),
    ] * 2


def test_unbound_and_disabled_gestures_do_nothing():
    rig = Rig()
    rig.look()
    rig.config.gestures.bindings["wink_left"] = "none"
    rig.wink()
    assert not rig.engine.gesture("not_a_gesture")
    assert rig.input.calls == []


def test_drag_follows_gaze_and_is_released_when_tracking_is_lost():
    rig = Rig()
    rig.config.gestures.bindings["long_blink"] = "drag_toggle"
    rig.look(0.2, 0.2)
    assert rig.engine.gesture("long_blink")
    rig.look(0.6, 0.6)
    assert rig.input.calls[-1] == ("move", 1151, 647)
    rig.engine.handle(TrackingState(TrackingStatus.LOST, 0.0, 0))
    assert rig.input.calls[-1] == ("button", "left", False)
    assert not rig.engine.dragging


def test_emergency_stop_releases_a_drag_and_disarms():
    rig = Rig()
    rig.config.gestures.bindings["long_blink"] = "drag_toggle"
    rig.look()
    rig.engine.gesture("long_blink")
    rig.engine.emergency_stop()
    assert rig.input.calls[-1] == ("button", "left", False)
    assert not rig.engine.armed
    rig.wink()
    assert rig.input.calls[-1] == ("button", "left", False)


def test_emergency_stop_disarms_even_when_releasing_the_drag_fails():
    rig = Rig()
    rig.config.gestures.bindings["long_blink"] = "drag_toggle"
    rig.look()
    rig.engine.gesture("long_blink")
    sent = len(rig.input.calls)
    rig.input.fail = True  # the session died with the button held
    rig.engine.emergency_stop()
    assert not rig.engine.armed and not rig.engine.dragging
    rig.input.fail = False
    rig.look()
    rig.wink()
    rig.engine.gesture("long_blink")
    assert len(rig.input.calls) == sent  # nothing more reaches the desktop until re-armed


def test_pause_gesture_works_while_disarmed():
    rig = Rig(armed=False)
    rig.config.gestures.bindings["head_nod"] = "pause_toggle"
    rig.engine.handle(Gesture("head_nod", GesturePhase.ENDED, 1.0, 0))
    assert rig.engine.armed


def test_dwell_fires_once_per_fixation():
    rig = Rig()
    rig.config.gestures.bindings["dwell"] = "left_click"
    rig.look()
    for duration in (300, 600, 900, 1200, 1500):
        rig.engine.handle(Fixation(0.5, 0.5, duration, 0.9, 0))
    assert len([c for c in rig.input.calls if c == ("button", "left", True)]) == 1
    rig.now += 1.0
    rig.look(0.7, 0.7)
    for duration in (300, 950):  # a new fixation elsewhere
        rig.engine.handle(Fixation(0.7, 0.7, duration, 0.9, 0))
    assert len([c for c in rig.input.calls if c == ("button", "left", True)]) == 2


def test_mouth_gesture_arms_the_microphone_then_toggles_voice():
    rig = Rig()
    rig.engine.handle(Gesture("mouth_open", GesturePhase.STARTED, 1.0, 0))
    rig.engine.handle(Gesture("mouth_open", GesturePhase.ENDED, 1.0, 0))
    assert rig.voice_arms == [True] and rig.voice_toggles == 1
    rig.engine.handle(Gesture("mouth_open", GesturePhase.CANCELLED, 1.0, 0))
    assert rig.voice_arms == [True, False]


def test_microphone_is_not_armed_while_disarmed():
    rig = Rig(armed=False)
    rig.engine.handle(Gesture("mouth_open", GesturePhase.STARTED, 1.0, 0))
    rig.engine.handle(Gesture("mouth_open", GesturePhase.ENDED, 1.0, 0))
    assert rig.voice_arms == [False] and rig.voice_toggles == 0


def test_injection_failure_disarms_instead_of_crashing():
    rig = Rig()
    rig.look()
    rig.input.fail = True
    rig.wink()
    assert not rig.engine.armed


@pytest.mark.parametrize(
    ("mode", "spoken", "calls", "outcome"),
    [
        ("auto", "Copy.", [("keys", "ctrl+c")], "Command"),
        ("auto", "Dear Anna", [("type", "Dear Anna ")], "Dictated"),
        ("commands", "Dear Anna", [], "Not a known command"),
        ("dictation", "copy", [("type", "copy ")], "Dictated"),
        ("auto", "close all windows", [], "Not supported yet"),
        ("auto", "stop listening", [], "Stopped listening"),
        ("auto", "scroll down", [("scroll", -6)], "Command"),
    ],
)
def test_utterance_routing(mode, spoken, calls, outcome, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    rig = Rig()
    rig.config.voice.mode = mode
    assert rig.engine.handle_utterance(spoken).startswith(outcome)
    assert rig.input.calls == calls


@pytest.mark.parametrize(
    ("platform", "opener"), [("linux", "super"), ("win32", "super"), ("darwin", "cmd+space")]
)
def test_launch_goes_through_the_desktop_launcher(monkeypatch, platform, opener):
    monkeypatch.setattr("sys.platform", platform)
    rig = Rig()
    assert rig.engine.run_command(Command("launch", "firefox"))
    assert rig.input.calls == [("keys", opener), ("type", "firefox"), ("keys", "enter")]


def test_engine_without_input_backend_cannot_be_armed():
    engine = InteractionEngine(Config(), DISPLAY, None)
    engine.set_armed(True)
    assert not engine.armed


def test_a_mistyped_custom_command_is_reported_and_presses_nothing():
    rig = Rig()
    rig.config.voice.custom_commands["open terminal"] = "key:ctrl+alt+tt"
    outcome = rig.engine.handle_utterance("open terminal")
    assert outcome.startswith("Command not valid") and "unknown key 'tt'" in outcome
    assert rig.input.calls == []
    assert rig.engine.armed  # a typo must not switch control off


def test_unknown_keys_never_reach_the_backend():
    notices = []
    rig = Rig(on_notice=notices.append)
    assert not rig.engine.run_command(Command("key", "ctrl+hyper"))
    assert rig.input.calls == [] and rig.engine.armed
    assert notices and "unknown key 'hyper'" in notices[0]
    assert rig.engine.run_command(Command("key", "Ctrl + C"))
    assert rig.input.calls == [("keys", "ctrl+c")]


def test_emergency_stop_interrupts_a_launch_and_is_not_kept_waiting():
    """A launch pauses between steps; the stop must not wait for it, nor let it continue."""
    import threading

    paused, resume = threading.Event(), threading.Event()

    def sleep(_seconds):
        paused.set()
        resume.wait(5)

    rig = Rig(sleep=sleep)
    worker = threading.Thread(target=rig.engine.run_command, args=(Command("launch", "firefox"),))
    worker.start()
    assert paused.wait(5)  # the launcher has opened; the launch is pausing
    stopped = threading.Event()
    threading.Thread(target=lambda: (rig.engine.emergency_stop(), stopped.set())).start()
    assert stopped.wait(1), "the emergency stop waited for the launch"
    resume.set()
    worker.join(5)
    assert [c[0] for c in rig.input.calls] == ["keys"]  # nothing typed after the stop


def test_clicks_on_a_combined_display_land_on_the_monitor_under_the_gaze():
    main = Display("main", 0, 0, 1920, 1080, primary=True)
    right = Display("right", 1920, 0, 1280, 1024)
    both = Display.combined([main, right])
    rig = Rig()
    rig.engine.display = both
    moved = []
    rig.input.move = lambda display, nx, ny: moved.append((display.name, nx, ny))
    rig.look(*both.normalize(1920 + 640, 512))
    rig.wink()
    assert moved and moved[0][0] == "right"
    assert moved[0][1:] == pytest.approx((0.5, 0.5), abs=1e-3)


def test_snapping_moves_the_click_only_when_enabled():
    asked = []

    def snap(display, nx, ny):
        asked.append((nx, ny))
        return (0.25, 0.25)

    rig = Rig(snap=snap)
    rig.look(0.5, 0.5)
    rig.wink()
    assert asked == [] and rig.input.calls[0] == ("move", 960, 540)
    rig.config.pointer.snap = True
    rig.now += 1
    rig.look(0.5, 0.5)
    rig.wink()
    assert asked == [(0.5, 0.5)] and ("move", 480, 270) in rig.input.calls


def test_no_snap_target_keeps_the_click_where_the_user_looks():
    rig = Rig(snap=lambda display, nx, ny: None)
    rig.config.pointer.snap = True
    rig.look(0.5, 0.5)
    rig.wink()
    assert rig.input.calls[0] == ("move", 960, 540)
