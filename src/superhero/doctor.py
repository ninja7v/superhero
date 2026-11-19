"""`superhero doctor`: what this machine offers SUPERHERO, without changing or prompting anything.

It is the first thing to run on a new platform, and the thing to paste into a bug report.
"""

from __future__ import annotations

import os
import platform
import sys


def _check(label: str, probe: object) -> tuple[str, str]:
    try:
        return label, str(probe())  # type: ignore[operator]
    except Exception as exc:
        return label, f"FAILED: {type(exc).__name__}: {exc}"


def _displays() -> str:
    from superhero.platform.displays import detect_displays

    return (
        "; ".join(
            f"{d.name} {d.width}x{d.height} at ({d.x},{d.y}) scale {d.scale:g}"
            + (" primary" if d.primary else "")
            for d in detect_displays()
        )
        or "none"
    )


def _cameras() -> str:
    from superhero.perception.camera import list_cameras

    return "; ".join(f"[{c.index}] {c.name}" for c in list_cameras()) or "none found"


def _microphones() -> str:
    from PySide6.QtMultimedia import QMediaDevices

    return "; ".join(d.description() for d in QMediaDevices.audioInputs()) or "none found"


def _landmarks() -> str:
    import mediapipe
    from fovea.paths import default_model_path

    cached = "cached" if default_model_path().is_file() else "will be downloaded on first run"
    return f"mediapipe {mediapipe.__version__}, face model {cached}"


def _speech() -> str:
    import faster_whisper

    return f"faster-whisper {faster_whisper.__version__} (model downloaded on first use)"


def _hotkey() -> str:
    from superhero.platform.base import desktop_entry_path, session_type

    if session_type() == "wayland":
        installed = desktop_entry_path().is_file()
        return "ready" if installed else "desktop entry installed at the next start"
    if sys.platform == "darwin":
        return "needs the Input Monitoring permission (macOS asks on first use)"
    return "available"


def _overlay() -> str:
    from PySide6.QtGui import QGuiApplication

    from superhero.platform.base import session_type

    name = QGuiApplication.platformName()
    if name in ("offscreen", "minimal"):
        return f"{name}: no display in use, so the laser cannot be checked here"
    if session_type() == "wayland" and name != "xcb":
        return f"{name}: LIMITED — a native Wayland window cannot stay on top; XWayland is needed"
    sampling = "no" if session_type() == "wayland" else "yes"
    return f"{name}: click-through, always on top; background colour sampling: {sampling}"


def _accessibility() -> str:
    from superhero.platform.accessibility import open_desktop, snapping_problem

    desktop = open_desktop()
    try:
        count = len(desktop.windows())
    finally:
        desktop.close()
    problem = snapping_problem()
    snapping = f"unavailable ({problem})" if problem else "available"
    return f"{count} windows readable; click snapping {snapping}"


def _ai() -> str:
    from superhero.ai.planner import list_models
    from superhero.config import AIConfig

    models = list_models(AIConfig(), timeout=1.5)
    return ", ".join(models) if models else "no local Ollama server found (optional)"


def report() -> list[tuple[str, str]]:
    from PySide6.QtGui import QGuiApplication

    import superhero
    from superhero.platform.base import input_status, session_type

    if session_type() == "wayland" and os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    rows = [
        ("SUPERHERO", superhero.__version__),
        ("System", f"{platform.platform()} ({platform.machine()})"),
        ("Python", platform.python_version()),
        ("Session", session_type()),
        _check("Displays", _displays),
        _check("Overlay", _overlay),
        _check("Cameras", _cameras),
        _check("Microphones", _microphones),
        _check("Input control", input_status),
        _check("Emergency shortcut", _hotkey),
        _check("Accessibility", _accessibility),
        _check("Gaze engine", _landmarks),
        _check("Speech", _speech),
        _check("AI models", _ai),
    ]
    del app
    return rows


def main() -> int:
    rows = report()
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label:<{width}}  {value}")
    # Non-zero only when a core component is broken, so CI can use this as a smoke test.
    core = {"Displays", "Overlay", "Gaze engine", "Speech"}
    return 1 if any(label in core and value.startswith("FAILED") for label, value in rows) else 0
