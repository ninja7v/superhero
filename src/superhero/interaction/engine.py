"""Turns perception events into desktop actions.

This is the only place that decides what an event means. It owns the safety
rules: nothing is injected unless control is armed, positional actions need a
fresh and confident gaze point, and losing tracking releases a held drag.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable

from fovea.events import (
    DoubleBlink,
    Eye,
    Fixation,
    GazePoint,
    Gesture,
    GesturePhase,
    LongBlink,
    TrackingState,
    TrackingStatus,
    Wink,
)

from superhero.ai.planner import AIUnavailable, Plan, make_plan
from superhero.config import AIConfig, Config
from superhero.interaction.commands import Command, normalize, parse, parse_chord
from superhero.platform.base import InputBackend
from superhero.platform.displays import Display

log = logging.getLogger(__name__)

_GAZE_FRESH_S = 0.5
_LAUNCH_STEP_S = 0.45
_PLAN_STEP_S = 0.25
_CONFIRM_WINDOW_S = 12.0
_YES = {"yes", "confirm", "do it", "run it", "go ahead", "ok", "okay"}
_NO = {"no", "cancel", "stop", "never mind"}


class InteractionEngine:
    def __init__(
        self,
        config: Config,
        display: Display,
        input_backend: InputBackend | None = None,
        *,
        on_voice_toggle: Callable[[], None] = lambda: None,
        on_voice_arm: Callable[[bool], None] = lambda armed: None,
        on_change: Callable[[], None] = lambda: None,
        on_notice: Callable[[str], None] = lambda text: None,
        planner: Callable[[str, AIConfig, str], Plan] = make_plan,
        desktop_context: Callable[[], str] = lambda: "",
        snap: Callable[[Display, float, float], tuple[float, float] | None] = lambda d, x, y: None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.display = display
        self.input = input_backend
        self.armed = False
        self.tracking = TrackingStatus.LOST
        self.pointer: tuple[float, float] | None = None  # display-normalized
        self.pointer_confident = False
        self.dragging = False
        self.last_gesture = ""
        self._on_voice_toggle = on_voice_toggle
        self._on_voice_arm = on_voice_arm
        self._on_change = on_change
        self._on_notice = on_notice
        self._planner = planner
        self._desktop_context = desktop_context
        self._snap = snap
        self._pending: Plan | None = None
        self._pending_until = 0.0
        self._clock = clock
        self._sleep = sleep
        self._pointer_at = float("-inf")
        self._last_fired: dict[str, float] = {}
        self._dwell_fired = False
        self._dwell_last_ms = 0.0
        # Commands run on the speech thread while gestures run on the UI thread.
        self._lock = threading.RLock()

    # -- state ---------------------------------------------------------------

    def set_armed(self, armed: bool) -> None:
        with self._lock:
            if not armed:
                self._release_drag()
            self.armed = armed and self.input is not None
        self._on_change()

    def emergency_stop(self) -> None:
        self._pending = None
        self.set_armed(False)

    @property
    def pointer_visible(self) -> bool:
        return self.config.tracking.eye and self.pointer is not None and self.pointer_confident

    # -- events --------------------------------------------------------------

    def handle(self, event: object) -> None:
        if isinstance(event, GazePoint):
            self._on_gaze(event)
        elif isinstance(event, TrackingState):
            self.tracking = event.status
            if event.status is TrackingStatus.LOST:
                self.pointer_confident = False
                with self._lock:
                    self._release_drag()
        elif isinstance(event, Wink):
            # Fovea names eyes by landmark topology; a mirrored frame swaps the sides.
            users_left = (event.eye is Eye.RIGHT) == self.config.camera.mirror
            self.gesture("wink_left" if users_left else "wink_right")
        elif isinstance(event, DoubleBlink):
            self.gesture("double_blink")
        elif isinstance(event, LongBlink):
            self.gesture("long_blink")
        elif isinstance(event, Fixation):
            self._on_fixation(event)
        elif isinstance(event, Gesture):
            if event.phase is GesturePhase.ENDED:
                self.gesture(event.kind)
            elif event.kind == "mouth_open" and self._binding("mouth_open") == "voice_toggle":
                self._on_voice_arm(event.phase is GesturePhase.STARTED and self.armed)

    def _on_gaze(self, event: GazePoint) -> None:
        self.pointer_confident = event.confidence >= self.config.pointer.min_confidence
        if not self.pointer_confident:
            return  # keep the last good position; the overlay hides the pointer
        self.pointer = (event.x, event.y)
        self._pointer_at = self._clock()
        if self.armed and (self.dragging or self.config.pointer.move_system_cursor):
            with self._lock:
                self._inject(lambda backend: self._move(backend, event.x, event.y))

    def _on_fixation(self, event: Fixation) -> None:
        if event.duration_ms < self._dwell_last_ms:
            self._dwell_fired = False  # a new fixation began
        self._dwell_last_ms = event.duration_ms
        if not self._dwell_fired and event.duration_ms >= self.config.gestures.dwell_ms:
            self._dwell_fired = True
            self.gesture("dwell")

    # -- gestures and actions --------------------------------------------------

    def _binding(self, gesture: str) -> str:
        return self.config.gestures.bindings.get(gesture, "none")

    def gesture(self, name: str) -> bool:
        """Run the action bound to a gesture. Returns whether an action ran."""
        action = self._binding(name)
        if action == "none":
            return False
        if self.pending_plan is not None and action in (
            "left_click",
            "double_click",
            "right_click",
        ):
            # While a plan awaits an answer, click gestures answer it instead of clicking.
            (self.cancel_plan if action == "right_click" else self.confirm_plan)()
            return True
        now = self._clock()
        if now - self._last_fired.get(name, float("-inf")) < self.config.gestures.cooldown_ms / 1e3:
            return False
        if not self.perform(action):
            return False
        self._last_fired[name] = now
        self.last_gesture = name
        self._on_change()
        return True

    def perform(self, action: str) -> bool:
        if action == "pause_toggle":  # the one action that works while disarmed
            self.set_armed(not self.armed)
            return True
        if not self.armed:
            return False
        if action == "voice_toggle":
            self._on_voice_toggle()
            return True
        with self._lock:
            if action in ("left_click", "right_click", "double_click", "drag_toggle"):
                return self._pointer_action(action)
            if action in ("scroll_up", "scroll_down"):
                lines = self.config.gestures.scroll_lines
                return self._inject(lambda b: b.scroll(lines if action == "scroll_up" else -lines))
        return False

    def _pointer_action(self, action: str) -> bool:
        fresh = self._clock() - self._pointer_at <= _GAZE_FRESH_S
        if self.dragging and action == "drag_toggle":
            return self._release_drag()  # always allowed: never leave a button held
        if self.pointer is None or not fresh or self.tracking is TrackingStatus.LOST:
            return False
        nx, ny = self.pointer
        if self.config.pointer.snap:
            # Gaze is off by a few degrees; the nearest button or link is the likely target.
            nx, ny = self._snap(self.display, nx, ny) or (nx, ny)

        def run(backend: InputBackend) -> None:
            self._move(backend, nx, ny)
            if action == "drag_toggle":
                backend.button("left", True)
                self.dragging = True
                return
            button = "right" if action == "right_click" else "left"
            for _ in range(2 if action == "double_click" else 1):
                backend.button(button, True)
                backend.button(button, False)

        return self._inject(run)

    def _move(self, backend: InputBackend, nx: float, ny: float) -> None:
        """Move the system pointer; on a combined display, onto the monitor under the point."""
        display, x, y = self.display.locate(nx, ny)
        backend.move(display, x, y)

    def _release_drag(self) -> bool:
        if not self.dragging:
            return False
        self.dragging = False
        return self._inject(lambda backend: backend.button("left", False))

    def _inject(self, call: Callable[[InputBackend], None]) -> bool:
        if self.input is None:
            return False
        try:
            call(self.input)
            return True
        except Exception:
            log.exception("input injection failed; disarming")
            self.armed = False
            self._on_change()
            return False

    # -- voice ---------------------------------------------------------------

    def run_command(self, command: Command) -> bool:
        """Execute a parsed voice command. Safe to call from the speech thread."""
        if command.kind == "action":
            return self.perform(command.value)
        if not self.armed or command.kind in ("unsupported", "stop", "invalid"):
            return False
        if command.kind == "launch":
            return self._launch(command.value)
        with self._lock:
            if command.kind == "key":
                try:
                    combo = parse_chord(command.value).split("+")
                except ValueError as exc:  # never hand an unknown key to the backend
                    self._on_notice(f"Command not run: {exc}")
                    return False
                return self._inject(lambda b: b.keys(combo))
            if command.kind == "type":
                return self._inject(lambda b: b.type_text(command.value))
            if command.kind == "scroll":
                lines = self.config.gestures.scroll_lines * 2
                return self._inject(lambda b: b.scroll(lines if command.value == "up" else -lines))
        return False

    def _launch(self, name: str) -> bool:
        """Open or switch to an app through the desktop's own launcher search."""
        opener = ["cmd", "space"] if sys.platform == "darwin" else ["super"]
        steps: list[Callable[[InputBackend], None]] = [
            lambda b: b.keys(opener),
            lambda b: b.type_text(name),
            lambda b: b.keys(["enter"]),
        ]
        for step in steps:
            # The lock is not held across the pause, so the emergency stop is never kept
            # waiting, and once it has run no further step is typed.
            with self._lock:
                if not self.armed or not self._inject(step):
                    return False
            self._sleep(_LAUNCH_STEP_S)
        return True

    def dictate(self, text: str) -> bool:
        if not self.armed or not text:
            return False
        with self._lock:
            return self._inject(lambda b: b.type_text(text))

    def handle_utterance(self, text: str) -> str:
        """Route recognised speech to a command or to dictation; returns what happened."""
        voice = self.config.voice
        phrase = normalize(text)
        if self.pending_plan is not None and phrase in _YES | _NO:
            return (
                "Running the plan" if phrase in _YES and self.confirm_plan() else self.cancel_plan()
            )
        ai = self.config.ai
        wake = normalize(ai.wake_word)
        if ai.enabled and wake and (phrase == wake or phrase.startswith(wake + " ")):
            return self._ask_ai(_after_wake_word(text, wake))
        command = None if voice.mode == "dictation" else parse(text, voice.custom_commands)
        if command is not None:
            if command.kind == "stop":
                return "Stopped listening"
            if command.kind == "unsupported":
                return f"Not supported yet: {command.value}"
            if command.kind == "invalid":
                return f"Command not valid ({command.value}) — fix it on the Voice page"
            return f"Command: {text}" if self.run_command(command) else "Command not run"
        if voice.mode == "commands":
            return "Not a known command"
        return "Dictated" if self.dictate(text + " ") else "Dictation not run"

    # -- AI plans --------------------------------------------------------------

    @property
    def pending_plan(self) -> Plan | None:
        """The plan awaiting confirmation, if it has not expired."""
        if self._pending is not None and self._clock() > self._pending_until:
            self._pending = None
        return self._pending

    @property
    def pending_text(self) -> str:
        plan = self.pending_plan
        if plan is None:
            return ""
        warning = "⚠ closes or deletes — " if plan.risky else ""
        return f"{warning}{plan.summary()}   ·   wink or say “yes” to run, right-wink to cancel"

    def _ask_ai(self, request: str) -> str:
        """Plan a free-form request. The plan is only proposed here, never run."""
        if not self.armed:
            return "Turn control on first"
        if not request.strip():
            return "Say what you want after the wake word"
        try:
            plan = self._planner(request, self.config.ai, self._desktop_context())
        except AIUnavailable as exc:
            return str(exc)
        if not plan.steps:
            return plan.explanation or "The AI could not plan that"
        self._pending, self._pending_until = plan, self._clock() + _CONFIRM_WINDOW_S
        self._on_change()
        return plan.explanation or "Plan ready"

    def cancel_plan(self) -> str:
        self._pending = None
        self._on_notice("Plan cancelled")
        return "Plan cancelled"

    def confirm_plan(self) -> bool:
        """Run the pending plan on a worker thread. Returns whether one was started."""
        plan = self.pending_plan
        self._pending = None
        if plan is None or not self.armed:
            return False
        threading.Thread(target=self._run_plan, args=(plan,), daemon=True).start()
        return True

    def _run_plan(self, plan: Plan) -> None:
        for number, step in enumerate(plan.steps, start=1):
            if not self.armed:  # the emergency stop also stops a plan midway
                self._on_notice(f"Plan stopped at step {number}")
                return
            if step.kind == "wait":
                self._sleep(float(step.value))
            elif not self.run_command(step):
                self._on_notice(f"Plan failed at step {number}: {step.kind} {step.value}")
                return
            self._sleep(_PLAN_STEP_S)
        self._on_notice("Done")


def _after_wake_word(text: str, wake: str) -> str:
    """The request that follows a (normalised, possibly multi-word) wake word in ``text``."""
    words = text.split()
    for count in range(1, len(words) + 1):
        if normalize(" ".join(words[:count])) == wake:
            return " ".join(words[count:])
    return ""
