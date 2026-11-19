"""Deterministic voice commands: a phrase table, no model involved.

A recognised utterance is normalised and looked up. A hit becomes a
:class:`Command`; a miss is left to dictation. Specs use one small grammar,
shared by the built-in table and user-defined commands::

    action:left_click     key:ctrl+shift+t     type:Kind regards
    scroll:down           launch:firefox       open:https://example.org
    stop

A spec that does not follow the grammar (a typo in a user's command) becomes an
``invalid`` command carrying the reason, so it is reported instead of reaching
the input backend.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from superhero.config import ACTIONS
from superhero.platform.base import KEY_NAMES

_FILLER = {"please", "this", "the", "current", "my", "a"}
_LAUNCH = re.compile(r"^(?:switch to|open|launch|start|go to) (.+)$")
# Spellings people and models use for the canonical key names.
_KEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "option": "alt", "win": "super", "windows": "super",
    "meta": "super", "command": "cmd", "esc": "escape", "return": "enter", "del": "delete",
    "pgup": "pageup", "pgdn": "pagedown", "page_up": "pageup", "page_down": "pagedown",
}  # fmt: skip
# Kinds of spec a user may write; ``unsupported`` and ``invalid`` are produced internally.
SPEC_KINDS = ("action", "key", "type", "scroll", "launch", "open", "stop")
_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+(:\d+)?(/\S*)?$")


@dataclass(frozen=True)
class Command:
    # action | key | type | scroll | launch | open | press | wait | stop | unsupported | invalid
    kind: str
    value: str = ""


def web_address(text: str) -> str:
    """A web address to open in the browser, or ``ValueError``.

    Only http and https: no ``file:``, ``javascript:`` or application schemes. A bare
    domain such as ``youtube.com/results?q=cats`` gets ``https://``.
    """
    url = text.strip()
    if _DOMAIN.match(url):
        url = "https://" + url
    parsed = urlparse(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or any(c.isspace() for c in url)
        or len(url) > 2000
    ):
        raise ValueError(f"not a web address: {text[:80]!r}")
    return url


def parse_chord(text: str) -> str:
    """Normalise a key chord (``Ctrl + Shift + T`` -> ``ctrl+shift+t``).

    Raises :class:`ValueError` naming the first key no input backend knows, so a typo
    is caught before anything is pressed.
    """
    keys = [part.strip().lower() for part in text.split("+")]
    if not text.strip() or any(not key for key in keys):
        raise ValueError(f"not a key chord: {text!r}")
    keys = [_KEY_ALIASES.get(key, key) for key in keys]
    for key in keys:
        if key not in KEY_NAMES and len(key) != 1:
            raise ValueError(f"unknown key {key!r}")
    return "+".join(keys)


def normalize(text: str) -> str:
    words = re.sub(r"[^\w\s+]", " ", text.lower()).split()
    return " ".join(w for w in words if w not in _FILLER)


def builtin_commands(platform: str = sys.platform) -> dict[str, str]:
    """Phrase -> spec for this platform. Window shortcuts are the desktop's own
    defaults, so applications receive an ordinary close request and keep their
    unsaved-changes prompts."""
    mac, win = platform == "darwin", platform == "win32"
    mod = "cmd" if mac else "ctrl"
    table = {
        "copy": f"key:{mod}+c",
        "paste": f"key:{mod}+v",
        "cut": f"key:{mod}+x",
        "undo": f"key:{mod}+z",
        "redo": "key:ctrl+y" if win else f"key:{mod}+shift+z",
        "select all": f"key:{mod}+a",
        "save": f"key:{mod}+s",
        "find": f"key:{mod}+f",
        "new tab": f"key:{mod}+t",
        "close tab": f"key:{mod}+w",
        "close window": "key:cmd+w" if mac else "key:alt+f4",
        "minimize window": "key:cmd+m" if mac else ("key:super+down" if win else "key:super+h"),
        "maximize window": "unsupported:macOS has no maximize shortcut" if mac else "key:super+up",
        "close all windows": "unsupported:closing every window needs window enumeration",
        "press enter": "key:enter",
        "press escape": "key:escape",
        "press tab": "key:tab",
        "delete that": f"key:{mod}+z",
        "scroll down": "scroll:down",
        "scroll up": "scroll:up",
        "page down": "key:pagedown",
        "page up": "key:pageup",
        "click": "action:left_click",
        "right click": "action:right_click",
        "double click": "action:double_click",
        "stop listening": "stop",
    }
    for verb in ("minimize", "maximize"):
        table[verb] = table[f"{verb} window"]
    table["close"] = table["close window"]
    return table


def parse_spec(spec: str) -> Command:
    """Turn a spec into a command; a malformed spec becomes ``Command("invalid", reason)``."""
    kind, _, value = spec.partition(":")
    kind, value = kind.strip().lower(), value.strip()
    if kind == "unsupported":
        return Command(kind, value)
    if kind not in SPEC_KINDS:
        return Command("invalid", f"unknown kind {kind!r}; use one of {', '.join(SPEC_KINDS)}")
    if kind == "key":
        try:
            return Command(kind, parse_chord(value))
        except ValueError as exc:
            return Command("invalid", str(exc))
    if kind == "action" and (value not in ACTIONS or value == "none"):
        return Command("invalid", f"unknown action {value!r}")
    if kind == "scroll" and value not in ("up", "down"):
        return Command("invalid", "scroll takes up or down")
    if kind in ("type", "launch") and not value:
        return Command("invalid", f"{kind} needs a value")
    if kind == "open":
        try:
            return Command(kind, web_address(value))
        except ValueError as exc:
            return Command("invalid", str(exc))
    return Command(kind, "" if kind == "stop" else value)


def spec_problem(spec: str) -> str:
    """Why a user-written spec cannot run, or an empty string when it is fine."""
    command = parse_spec(spec)
    return command.value if command.kind == "invalid" else ""


def lookup(
    text: str, custom: dict[str, str] | None = None, platform: str = sys.platform
) -> Command | None:
    """The command a phrase of the table (built-in or the user's) names, or ``None``."""
    phrase = normalize(text)
    if not phrase:
        return None
    table = builtin_commands(platform)
    table.update({normalize(k): v for k, v in (custom or {}).items()})
    return parse_spec(table[phrase]) if phrase in table else None


def parse(
    text: str, custom: dict[str, str] | None = None, platform: str = sys.platform
) -> Command | None:
    """The command an utterance names, or ``None`` if it is not a command."""
    command = lookup(text, custom, platform)
    if command is not None:
        return command
    launch = _LAUNCH.match(normalize(text))
    if launch and len(launch.group(1).split()) <= 3:
        return Command("launch", launch.group(1))
    return None
