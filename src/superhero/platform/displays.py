"""Display geometry and gaze-to-pixel mapping.

Gaze is calibrated per display in display-normalized coordinates, so a display
is all the mapping needs. Two pixel spaces matter:

* logical: what Qt lays windows out in (the overlay uses this);
* native: what OS input injection expects, which differs per platform.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Display:
    name: str
    x: int  # logical origin; may be negative for displays left of / above the primary
    y: int
    width: int  # logical size
    height: int
    scale: float = 1.0  # device pixel ratio
    primary: bool = False
    width_mm: float = 0.0
    height_mm: float = 0.0

    @property
    def portrait(self) -> bool:
        return self.height > self.width

    def logical_point(self, nx: float, ny: float) -> tuple[int, int]:
        """Global logical pixel for a display-normalized gaze point (clamped)."""
        cx, cy = min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0)
        return (
            self.x + round(cx * (self.width - 1)),
            self.y + round(cy * (self.height - 1)),
        )

    def native_point(self, nx: float, ny: float, platform: str = sys.platform) -> tuple[int, int]:
        """Global pixel in the coordinate space the platform's input injection uses."""
        lx, ly = self.logical_point(nx, ny)
        if platform == "darwin":
            # Quartz events are in points, which is Qt's logical space.
            return lx, ly
        if platform == "win32":
            # Per-monitor DPI aware: Qt keeps each screen's origin in native pixels
            # and scales only the extent.
            return (
                self.x + round((lx - self.x) * self.scale),
                self.y + round((ly - self.y) * self.scale),
            )
        # X11: one global scale applied to the whole logical desktop.
        return round(lx * self.scale), round(ly * self.scale)

    def error_to_degrees(self, error_norm: float, distance_mm: float = 600.0) -> float | None:
        """Convert a display-normalized error to visual angle, if physical size is known."""

        if self.width_mm <= 0 or self.height_mm <= 0:
            return None
        size_mm = error_norm * math.hypot(self.width_mm, self.height_mm) / math.sqrt(2.0)
        return math.degrees(math.atan2(size_mm, distance_mm))


def pick_display(displays: list[Display], name: str) -> Display:
    """The named display, else the primary, else the first."""
    if not displays:
        raise ValueError("no displays detected")
    for display in displays:
        if display.name == name:
            return display
    return next((d for d in displays if d.primary), displays[0])


def detect_displays() -> list[Display]:
    """Enumerate displays through Qt (requires a running QGuiApplication)."""
    from PySide6.QtGui import QGuiApplication

    primary = QGuiApplication.primaryScreen()
    result = []
    for screen in QGuiApplication.screens():
        geo, phys = screen.geometry(), screen.physicalSize()
        result.append(
            Display(
                name=screen.name(),
                x=geo.x(),
                y=geo.y(),
                width=geo.width(),
                height=geo.height(),
                scale=float(screen.devicePixelRatio()),
                primary=screen is primary,
                width_mm=float(phys.width()),
                height_mm=float(phys.height()),
            )
        )
    return result
