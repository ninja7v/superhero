"""User configuration: typed settings, JSON profiles, import/export.

One profile is one JSON file. Loading is forgiving on purpose: unknown keys are
ignored and missing keys take their defaults, which is the migration path for
additive changes. ``version`` exists for the day a change is not additive.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir, user_data_dir

log = logging.getLogger(__name__)

APP_NAME = "superhero"
CONFIG_VERSION = 1
DEFAULT_PROFILE = "default"

# Gesture and action vocabularies. The control panel builds its tables from these.
GESTURES: dict[str, str] = {
    "wink_left": "Left wink",
    "wink_right": "Right wink",
    "double_blink": "Double blink",
    "long_blink": "Long blink (both eyes)",
    "dwell": "Sustained gaze (dwell)",
    "mouth_open": "Mouth held open",
    "head_tilt_left": "Head tilted left",
    "head_tilt_right": "Head tilted right",
    "head_nod": "Head nod",
    "hand_pinch": "Hand: thumb and index pinch",
    "hand_two_fingers": "Hand: two fingers up",
    "hand_open_palm": "Hand: open palm",
    "hand_fist": "Hand: fist",
}
ACTIONS: dict[str, str] = {
    "none": "Do nothing",
    "left_click": "Left click",
    "right_click": "Right click",
    "double_click": "Double click",
    "drag_toggle": "Start / stop dragging",
    "scroll_up": "Scroll up",
    "scroll_down": "Scroll down",
    "voice_toggle": "Start / stop voice mode",
    "pause_toggle": "Pause / resume control",
}
# Head gestures need head tracking; mouth gestures need mouth tracking.
GESTURE_REQUIRES: dict[str, str] = {
    "mouth_open": "mouth",
    "head_tilt_left": "head",
    "head_tilt_right": "head",
    "head_nod": "head",
    "hand_pinch": "hand",
    "hand_two_fingers": "hand",
    "hand_open_palm": "hand",
    "hand_fist": "hand",
}


@dataclass
class CameraConfig:
    index: int = 0
    # 1280x720 puts about twice as many pixels across the iris as 640x480, which is what
    # limits webcam gaze accuracy. The camera falls back to 640x480 if it cannot keep up.
    width: int = 1280
    height: int = 720
    fps: int = 30
    mirror: bool = True


@dataclass
class TrackingConfig:
    eye: bool = True
    mouth: bool = True
    head: bool = False
    hand: bool = False


@dataclass
class PointerConfig:
    size: int = 8
    color: str = "auto"  # "auto" or "#rrggbb"
    style: str = "dot"  # "dot" | "ring" | "crosshair"
    pulse: bool = True
    # One Euro filter: lower mincutoff = steadier at rest, higher beta = snappier on moves.
    smoothing_mincutoff: float = 1.2
    smoothing_beta: float = 0.02
    min_confidence: float = 0.35
    move_system_cursor: bool = False
    # Clicks go to the nearest button, link or field within snap_radius (logical pixels),
    # found through the accessibility tree.
    snap: bool = False
    snap_radius: int = 48


@dataclass
class GestureConfig:
    bindings: dict[str, str] = field(
        default_factory=lambda: {
            "wink_left": "left_click",
            "wink_right": "right_click",
            "double_blink": "double_click",
            "long_blink": "none",
            "dwell": "none",
            "mouth_open": "voice_toggle",
            "head_tilt_left": "scroll_up",
            "head_tilt_right": "scroll_down",
            "head_nod": "none",
            "hand_pinch": "left_click",
            "hand_two_fingers": "right_click",
            "hand_open_palm": "pause_toggle",
            "hand_fist": "drag_toggle",
        }
    )
    cooldown_ms: int = 400
    dwell_ms: int = 900
    wink_min_ms: int = 120
    wink_max_ms: int = 700
    double_blink_ms: int = 500
    long_blink_ms: int = 600
    mouth_open_ms: int = 1000
    mouth_open_threshold: float = 0.45
    head_tilt_deg: float = 14.0
    head_nod_deg: float = 10.0
    hand_hold_ms: int = 400
    scroll_lines: int = 3


@dataclass
class VoiceConfig:
    mode: str = "auto"  # "auto" (commands, else dictation) | "commands" | "dictation"
    language: str = "en"
    model: str = "base"  # faster-whisper model size
    silence_timeout_s: float = 1.5
    max_seconds: float = 20.0
    prebuffer_ms: int = 600
    microphone: str = ""  # empty = system default
    # phrase -> action spec ("action:<name>", "key:ctrl+alt+t", "type:<text>", "launch:<app>")
    custom_commands: dict[str, str] = field(default_factory=dict)


@dataclass
class AIConfig:
    enabled: bool = False
    url: str = "http://localhost:11434"  # an Ollama server
    model: str = ""
    wake_word: str = "computer"  # requests starting with this word go to the AI
    allow_remote: bool = False  # explicit consent to send requests to another machine
    share_windows: bool = True  # tell the model which windows are open (names and titles)


@dataclass
class Config:
    version: int = CONFIG_VERSION
    camera: CameraConfig = field(default_factory=CameraConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    pointer: PointerConfig = field(default_factory=PointerConfig)
    gestures: GestureConfig = field(default_factory=GestureConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    active_display: str = ""  # display name; empty = primary; "*" = all displays together
    calibration_points: int = 9
    emergency_hotkey: str = "ctrl+alt+escape"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        return _merge(cls(), data)


# Valid values, shared by profile loading and the control panel. A profile can be edited by
# hand or imported, so every value is checked on load: out of range is clamped, anything
# else invalid keeps its default. The ranges are the panel's.
RANGES: dict[type, dict[str, tuple[float, float]]] = {
    CameraConfig: {"index": (0, 63), "width": (160, 3840), "height": (120, 2160), "fps": (10, 120)},
    PointerConfig: {
        "size": (3, 40), "smoothing_mincutoff": (0.2, 5.0), "smoothing_beta": (0.0, 0.5),
        "min_confidence": (0.0, 1.0), "snap_radius": (8, 200),
    },
    GestureConfig: {
        "cooldown_ms": (0, 3000), "dwell_ms": (300, 5000), "wink_min_ms": (50, 500),
        "wink_max_ms": (200, 2000), "double_blink_ms": (200, 1500), "long_blink_ms": (300, 3000),
        "mouth_open_ms": (300, 5000), "mouth_open_threshold": (0.15, 0.9),
        "head_tilt_deg": (5, 40), "head_nod_deg": (4, 30), "hand_hold_ms": (100, 3000),
        "scroll_lines": (1, 20),
    },
    VoiceConfig: {"silence_timeout_s": (0.5, 10), "max_seconds": (3, 120), "prebuffer_ms": (0, 2000)},
}  # fmt: skip
CHOICES: dict[type, dict[str, tuple[Any, ...]]] = {
    PointerConfig: {"style": ("dot", "ring", "crosshair")},
    VoiceConfig: {"mode": ("auto", "commands", "dictation")},
    Config: {"calibration_points": (5, 9, 13)},
}
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# Dictionaries whose keys and values come from fixed vocabularies.
_VOCABULARIES: dict[tuple[type, str], tuple[dict[str, str], dict[str, str]]] = {
    (GestureConfig, "bindings"): (GESTURES, ACTIONS),
}


def _valid(target: Any, name: str, value: Any) -> bool:
    choices = CHOICES.get(type(target), {}).get(name)
    if choices is not None and value not in choices:
        return False
    if isinstance(target, PointerConfig) and name == "color":
        return value == "auto" or bool(_COLOR.match(value))
    if isinstance(target, VoiceConfig) and name == "model":
        return bool(value.strip())
    return True


def _merge(target: Any, data: Any) -> Any:
    """Copy known, valid keys from ``data`` onto a default instance."""
    if not isinstance(data, dict):
        return target
    limits = RANGES.get(type(target), {})
    for f in fields(target):
        if f.name not in data:
            continue
        current, value = getattr(target, f.name), data[f.name]
        if is_dataclass(current):
            _merge(current, value)
        elif isinstance(current, dict):
            if isinstance(value, dict):
                keys, values = _VOCABULARIES.get((type(target), f.name), (None, None))
                current.update(
                    {
                        str(k): str(v)
                        for k, v in value.items()
                        if (keys is None or k in keys) and (values is None or v in values)
                    }
                )
        elif isinstance(current, bool):
            if isinstance(value, bool):
                setattr(target, f.name, value)
        elif isinstance(current, int | float):
            if isinstance(value, int | float) and not isinstance(value, bool):
                if not math.isfinite(value):
                    continue
                if f.name in limits:
                    lo, hi = limits[f.name]
                    value = min(max(value, lo), hi)
                value = type(current)(value)
                if _valid(target, f.name, value):
                    setattr(target, f.name, value)
        elif isinstance(current, str) and isinstance(value, str) and _valid(target, f.name, value):
            setattr(target, f.name, value)
    return target


def config_dir() -> Path:
    override = os.environ.get("SUPERHERO_CONFIG_DIR")
    return Path(override).expanduser() if override else Path(user_config_dir(APP_NAME))


def data_dir() -> Path:
    override = os.environ.get("SUPERHERO_DATA_DIR")
    return Path(override).expanduser() if override else Path(user_data_dir(APP_NAME))


def profile_path(name: str = DEFAULT_PROFILE) -> Path:
    if not name or Path(name).name != name:
        raise ValueError("profile name must be a single path component")
    return config_dir() / "profiles" / f"{name}.json"


def list_profiles() -> list[str]:
    folder = config_dir() / "profiles"
    names = sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []
    return names or [DEFAULT_PROFILE]


def read_file(path: Path) -> Config:
    """Read a settings file strictly: raises ``OSError`` or ``ValueError`` if it is unusable."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("this is not a SUPERHERO settings file")
    return Config.from_dict(data)


def load_file(path: Path) -> Config:
    """Read a config file. A missing or unreadable file yields the defaults.

    A file that exists but cannot be parsed is renamed to ``*.bad`` first, so falling back
    to the defaults (which are saved at the next change) never destroys what was there.
    """
    try:
        return read_file(path)
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError) as exc:
        backup = path.with_name(path.name + ".bad")
        log.warning("Settings in %s are unreadable (%s); kept as %s", path, exc, backup.name)
        with contextlib.suppress(OSError):
            os.replace(path, backup)
        return Config()


def save_file(config: Config, path: Path) -> None:
    """Write atomically so a crash mid-save never leaves a truncated profile."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config.to_dict(), handle, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load(name: str = DEFAULT_PROFILE) -> Config:
    return load_file(profile_path(name))


def save(config: Config, name: str = DEFAULT_PROFILE) -> None:
    save_file(config, profile_path(name))


def calibration_path(display_name: str, camera_index: int) -> Path:
    """One calibration file per (display, camera): calibrations are not transferable."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in display_name) or "display"
    return data_dir() / "calibration" / f"{safe}-cam{camera_index}.json"
