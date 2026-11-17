"""Display geometry and gaze-to-pixel mapping.

Gaze is calibrated per display in display-normalized coordinates, so a display
is all the mapping needs. Two pixel spaces matter:

* logical: what Qt lays windows out in (the overlay uses this);
* native: what OS input injection expects, which differs per platform.

Several monitors can also be used as one: :meth:`Display.combined` spans them all,
gaze is calibrated across the whole area, and :meth:`Display.locate` finds which
monitor a point is on.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass

# The ``active_display`` setting that selects every display at once.
ALL_DISPLAYS = "*"


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
    # The monitors of a combined display, left to right; empty for a single monitor.
    parts: tuple[Display, ...] = ()

    @classmethod
    def combined(cls, displays: Sequence[Display]) -> Display:
        """One display spanning ``displays``: the bounding box of their logical geometry."""
        left = min(d.x for d in displays)
        top = min(d.y for d in displays)
        right = max(d.x + d.width for d in displays)
        bottom = max(d.y + d.height for d in displays)
        parts = tuple(sorted(displays, key=lambda d: (d.x, d.y)))
        return cls(ALL_DISPLAYS, left, top, right - left, bottom - top, parts=parts)

    @property
    def identity(self) -> str:
        """What a calibration belongs to. A combined one includes the arrangement, so moving
        a monitor makes the old calibration not apply."""
        if not self.parts:
            return self.name
        return "all_" + "_".join(f"{p.name}@{p.x},{p.y},{p.width}x{p.height}" for p in self.parts)

    @property
    def portrait(self) -> bool:
        return self.height > self.width

    def locate(self, nx: float, ny: float) -> tuple[Display, float, float]:
        """The monitor a normalized point is on, and the point normalized to that monitor.

        A point in a gap of a combined display (monitors of different sizes leave some) goes
        to the nearest monitor's edge.
        """
        if not self.parts:
            return self, nx, ny
        lx, ly = self.logical_point(nx, ny)

        def gap(d: Display) -> float:
            dx = max(d.x - lx, 0, lx - (d.x + d.width - 1))
            dy = max(d.y - ly, 0, ly - (d.y + d.height - 1))
            return math.hypot(dx, dy)

        part = min(self.parts, key=gap)
        px = min(max(lx, part.x), part.x + part.width - 1)
        py = min(max(ly, part.y), part.y + part.height - 1)
        return part, (px - part.x) / max(part.width - 1, 1), (py - part.y) / max(part.height - 1, 1)

    def normalize(self, lx: float, ly: float) -> tuple[float, float]:
        """The normalized position of a global logical pixel (the inverse of logical_point)."""
        return (lx - self.x) / max(self.width - 1, 1), (ly - self.y) / max(self.height - 1, 1)

    def logical_point(self, nx: float, ny: float) -> tuple[int, int]:
        """Global logical pixel for a display-normalized gaze point (clamped)."""
        cx, cy = min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0)
        return (
            self.x + round(cx * (self.width - 1)),
            self.y + round(cy * (self.height - 1)),
        )

    def native_point(self, nx: float, ny: float, platform: str = sys.platform) -> tuple[int, int]:
        """Global pixel in the coordinate space the platform's input injection uses."""
        if self.parts:
            part, px, py = self.locate(nx, ny)
            return part.native_point(px, py, platform)
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

    def from_native(self, x: float, y: float, platform: str = sys.platform) -> tuple[float, float]:
        """Global logical pixel for a native one: the inverse of :meth:`native_point` for a
        point on this (single) display."""
        if platform == "darwin":
            return x, y
        if platform == "win32":
            return self.x + (x - self.x) / self.scale, self.y + (y - self.y) / self.scale
        return x / self.scale, y / self.scale


def pick_display(displays: list[Display], name: str) -> Display:
    """The named display, all of them combined, else the primary, else the first."""
    if not displays:
        raise ValueError("no displays detected")
    if name == ALL_DISPLAYS and len(displays) > 1:
        return Display.combined(displays)
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
