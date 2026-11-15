"""AI command interpretation: free-form speech -> a short plan of ordinary input actions.

The model never touches the desktop. It returns a plan made only of the same
primitives the phrase table uses (key chords, typing, launcher search, scroll,
click, short waits). The plan is validated here, shown to the user, and runs
only after they confirm.

The default backend is a local Ollama server. A non-local server URL is refused
unless the user has explicitly allowed remote AI.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from superhero.config import AIConfig
from superhero.interaction.commands import Command, parse_chord

MAX_STEPS = 10
MAX_TYPED = 500
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Chords that close things or delete. They are allowed, but the confirmation says so.
_RISKY_KEYS = {"alt+f4", "cmd+q", "cmd+w", "ctrl+w", "ctrl+shift+w", "ctrl+q", "delete",
               "shift+delete", "cmd+backspace", "ctrl+shift+delete"}  # fmt: skip

_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["key", "type", "launch", "scroll", "click", "wait"],
                    },
                    "value": {"type": "string"},
                },
                "required": ["kind", "value"],
            },
        },
        "explanation": {"type": "string"},
    },
    "required": ["steps", "explanation"],
}

_SYSTEM = """You turn a spoken request into a short plan of input actions for the user's \
desktop ({platform}). You cannot see the screen. Reply with JSON only.

Step kinds:
- key: one key chord, e.g. "ctrl+t", "alt+tab", "enter". Modifiers: ctrl, alt, shift, {super}.
- type: literal text to type into the focused field.
- launch: an application or folder name; opens the system launcher, searches for it and presses enter.
- scroll: "up" or "down".
- click: "left", "right" or "double" at the point the user is looking at.
- wait: seconds to pause, e.g. "0.5", after something that takes time to open.

Rules:
- Use at most {max_steps} steps and prefer the fewest.
- After a launch, add a wait of 1 before typing into the new window.
- Only plan what these steps can really do. You cannot read files, read the screen, or know \
which windows are open. If the request needs that, or is unclear, return no steps and say why \
in "explanation".
- Never plan anything that deletes data, discards unsaved work, sends messages, or buys \
anything unless the request says so explicitly.
- "explanation" is one short sentence describing what the plan does.

Example reply for "open the calculator and type two plus two":
{{"steps": [{{"kind": "launch", "value": "calculator"}}, {{"kind": "wait", "value": "1"}}, \
{{"kind": "type", "value": "2+2"}}], "explanation": "Opens the calculator and types 2+2."}}"""


class AIUnavailable(RuntimeError):
    """The AI backend is off, unreachable, not permitted, or returned something unusable."""


@dataclass(frozen=True)
class Plan:
    steps: tuple[Command, ...]
    explanation: str
    risky: bool = False

    def summary(self) -> str:
        parts = [f"{s.kind} {s.value}".strip() for s in self.steps]
        return " → ".join(parts)


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in _LOCAL_HOSTS


def validate(raw: object) -> Plan:
    """Turn a model reply into a Plan, rejecting anything outside the action vocabulary."""
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        raise AIUnavailable("The AI reply was not a plan.")
    explanation = str(raw.get("explanation", "")).strip()[:200]
    if len(raw["steps"]) > MAX_STEPS:
        raise AIUnavailable(f"The AI planned more than {MAX_STEPS} steps; refusing.")
    steps: list[Command] = []
    risky = False
    for item in raw["steps"]:
        if not isinstance(item, dict):
            raise AIUnavailable("The AI reply contained a malformed step.")
        kind, value = _read_step(item)
        if kind == "key":
            try:
                chord = parse_chord(value)
            except ValueError as exc:
                raise AIUnavailable(f"The AI used an unknown key: {value!r}.") from exc
            risky = risky or chord in _RISKY_KEYS
            steps.append(Command("key", chord))
        elif kind == "type":
            if not value or len(value) > MAX_TYPED:
                raise AIUnavailable("The AI wanted to type nothing or far too much.")
            steps.append(Command("type", value))
        elif kind == "launch":
            if not value.strip() or len(value) > 60 or "\n" in value:
                raise AIUnavailable("The AI gave an unusable application name.")
            steps.append(Command("launch", value.strip()))
        elif kind == "scroll" and value in ("up", "down"):
            steps.append(Command("scroll", value))
        elif kind == "click" and value in ("left", "right", "double"):
            steps.append(Command("action", f"{value}_click"))
        elif kind == "wait":
            try:
                seconds = min(max(float(value), 0.0), 3.0)
            except ValueError:
                seconds = 1.0  # models often garble this number; a pause is harmless
            steps.append(Command("wait", str(seconds)))
        else:
            raise AIUnavailable(f"The AI used an action that is not allowed: {kind} {value!r}.")
    return Plan(tuple(steps), explanation, risky)


_KINDS = ("key", "type", "launch", "scroll", "click", "wait")


def _read_step(item: dict[str, object]) -> tuple[str, str]:
    """Accept the spellings small models actually produce; the vocabulary stays strict.

    Seen in practice: ``{"kind": "key", "value": "ctrl+t"}`` (as asked),
    ``{"type": "launch", "value": "firefox"}`` and the shorthand ``{"wait": "1"}``.
    """
    for label in ("kind", "type", "action"):
        if item.get(label) in _KINDS and "value" in item:
            return str(item[label]), str(item["value"])
    if len(item) == 1:
        ((kind, value),) = item.items()
        return str(kind), str(value)
    return str(item.get("kind", "")), str(item.get("value", ""))


def list_models(config: AIConfig, timeout: float = 3.0) -> list[str]:
    """Models installed on the configured Ollama server; empty if it cannot be reached."""
    if not (is_local(config.url) or config.allow_remote):
        return []
    try:
        with urllib.request.urlopen(f"{config.url.rstrip('/')}/api/tags", timeout=timeout) as reply:
            return sorted(m["name"] for m in json.load(reply).get("models", []))
    except (OSError, ValueError, KeyError):
        return []


def make_plan(
    request: str, config: AIConfig, platform: str = sys.platform, timeout: float = 60.0
) -> Plan:
    """Ask the model for a plan. Raises :class:`AIUnavailable` with a user-readable reason."""
    if not config.enabled:
        raise AIUnavailable("AI commands are turned off.")
    if not (is_local(config.url) or config.allow_remote):
        raise AIUnavailable("The AI server is not on this computer and remote AI is not allowed.")
    if not config.model:
        raise AIUnavailable("No AI model is selected.")
    names = {"linux": "Linux, GNOME-style shortcuts", "win32": "Windows 11", "darwin": "macOS"}
    system = _SYSTEM.format(
        platform=names.get(platform, platform),
        super="cmd" if platform == "darwin" else "super",
        max_steps=MAX_STEPS,
    )
    body = {
        "model": config.model,
        "stream": False,
        "think": False,
        "format": _SCHEMA,
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": request}],
    }
    http = urllib.request.Request(
        f"{config.url.rstrip('/')}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(http, timeout=timeout) as reply:
            content = json.load(reply)["message"]["content"]
        return validate(json.loads(content))
    except urllib.error.HTTPError as exc:
        raise AIUnavailable(f"The AI server refused the request ({exc.code}).") from exc
    except OSError as exc:
        raise AIUnavailable("The AI server cannot be reached. Is Ollama running?") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise AIUnavailable("The AI reply could not be understood.") from exc
