"""The laser pointer: a click-through, always-on-top overlay on one display.

The overlay never takes focus or input, so the desktop underneath behaves
exactly as if it were not there. Only the small region around the pointer is
repainted per gaze update.
"""

from __future__ import annotations

import math
import sys
import time
from collections import deque

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from superhero.config import PointerConfig
from superhero.platform.base import session_type
from superhero.platform.displays import Display

_ON_LIGHT = QColor("#ff2d2d")  # red reads best on light backgrounds
_ON_DARK = QColor("#22e0ff")  # cyan on dark ones
_SAMPLE_EVERY_S = 0.2


def contrast_color(luminance: float) -> QColor:
    """Pointer colour for a background luminance in [0, 1]."""
    return _ON_LIGHT if luminance >= 0.5 else _ON_DARK


def paint_pointer(
    painter: QPainter, pos: QPointF, cfg: PointerConfig, color: QColor, phase: float = 0.5
) -> None:
    """Draw the laser. Shared by the overlay and the settings preview so they never differ.

    ``phase`` in [0, 1] drives the pulse of the glow.
    """
    radius = cfg.size / 2
    glow_radius = radius * (2.2 + 0.8 * phase)
    glow = QRadialGradient(pos, glow_radius)
    halo = QColor(color)
    halo.setAlphaF(0.55)
    glow.setColorAt(0.0, halo)
    halo.setAlphaF(0.0)
    glow.setColorAt(1.0, halo)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(pos, glow_radius, glow_radius)

    # Dual outline: dark then light, so the pointer survives any background.
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(0, 0, 0, 200), 2.0))
    painter.drawEllipse(pos, radius + 2.0, radius + 2.0)
    painter.setPen(QPen(QColor(255, 255, 255, 235), 1.5))
    painter.drawEllipse(pos, radius + 0.5, radius + 0.5)

    if cfg.style == "ring":
        painter.setPen(QPen(color, max(2.0, radius * 0.5)))
        painter.drawEllipse(pos, radius - 1.0, radius - 1.0)
    elif cfg.style == "crosshair":
        painter.setPen(QPen(color, 2.0))
        painter.drawLine(QPointF(pos.x() - radius, pos.y()), QPointF(pos.x() + radius, pos.y()))
        painter.drawLine(QPointF(pos.x(), pos.y() - radius), QPointF(pos.x(), pos.y() + radius))
    else:
        core = QRadialGradient(pos, radius)
        core.setColorAt(0.0, QColor(255, 255, 255))
        core.setColorAt(0.45, color)
        core.setColorAt(1.0, color.darker(130))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(pos, radius, radius)


class Overlay(QWidget):
    def __init__(self, config: PointerConfig, display: Display) -> None:
        super().__init__()
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if sys.platform.startswith("linux"):
            # Override-redirect keeps the overlay above every window, including under XWayland.
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        self.setWindowFlags(flags)
        self.setWindowTitle("SUPERHERO pointer")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # macOS hides tool windows whenever another application is in front; the laser must stay.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._config = config
        self._display = display
        self._pos: QPointF | None = None
        self._visible = False
        self._status = ""
        self._tracking_on = False
        self._color = _ON_LIGHT
        self._last_sample = 0.0
        self._pending_capture_ns: int | None = None
        self.latencies_ms: deque[float] = deque(maxlen=300)
        # Wayland clients cannot read the screen without a capture session, so automatic
        # colour falls back to the fixed colour plus the dual outline there.
        self.can_sample = session_type() != "wayland"
        self._pulse = QTimer(self, interval=33)
        self._pulse.timeout.connect(self._repaint_pointer)
        self.set_display(display)

    def set_display(self, display: Display) -> None:
        self._display = display
        self.setGeometry(display.x, display.y, display.width, display.height)

    # -- updates ---------------------------------------------------------------

    def _pointer_rect(self, pos: QPointF | None) -> QRect:
        if pos is None:
            return QRect()
        r = self._config.size * 3 + 6
        return QRect(int(pos.x()) - r, int(pos.y()) - r, 2 * r, 2 * r)

    def _repaint_pointer(self) -> None:
        self.update(self._pointer_rect(self._pos))

    def set_pointer(
        self, point: tuple[float, float] | None, visible: bool, captured_ns: int | None = None
    ) -> None:
        old = self._pointer_rect(self._pos) if self._visible else QRect()
        self._visible = visible and point is not None
        if point is not None:
            self._pos = QPointF(
                point[0] * (self._display.width - 1), point[1] * (self._display.height - 1)
            )
        self._pending_capture_ns = captured_ns if self._visible else None
        if self._visible and self._config.pulse != self._pulse.isActive():
            self._pulse.setInterval(33)
            (self._pulse.start if self._config.pulse else self._pulse.stop)()
        elif not self._visible:
            self._pulse.stop()
        if self._visible and self._config.color == "auto" and self.can_sample:
            self._sample_background()
        self.update(old.united(self._pointer_rect(self._pos)))

    def set_status(self, text: str, tracking_on: bool) -> None:
        if (text, tracking_on) != (self._status, self._tracking_on):
            self._status, self._tracking_on = text, tracking_on
            self.update(QRect(0, 0, self.width(), 64))

    def _sample_background(self) -> None:
        now = time.monotonic()
        if now - self._last_sample < _SAMPLE_EVERY_S or self._pos is None:
            return
        self._last_sample = now
        screen = self.screen() or QGuiApplication.primaryScreen()
        r = self._config.size * 3
        shot = screen.grabWindow(0, int(self._pos.x()) - r, int(self._pos.y()) - r, 2 * r, 2 * r)
        if shot.isNull():
            self.can_sample = False
            return
        image = shot.toImage().scaled(8, 8)
        # Border pixels only: the centre contains the pointer itself.
        border = [(x, y) for x in range(8) for y in range(8) if x in (0, 7) or y in (0, 7)]
        luminance = sum(image.pixelColor(x, y).lightnessF() for x, y in border) / len(border)
        # Hysteresis so the colour does not flicker on mid-grey backgrounds.
        if abs(luminance - 0.5) > 0.08:
            self._color = contrast_color(luminance)

    # -- painting ----------------------------------------------------------------

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._tracking_on:
            self._paint_indicator(painter)
        if self._status:
            self._paint_status(painter)
        if self._visible and self._pos is not None:
            self._paint_pointer(painter, self._pos)
            if self._pending_capture_ns is not None:
                self.latencies_ms.append((time.monotonic_ns() - self._pending_capture_ns) / 1e6)
                self._pending_capture_ns = None
        painter.end()

    def _paint_pointer(self, painter: QPainter, pos: QPointF) -> None:
        cfg = self._config
        color = self._color if cfg.color == "auto" else QColor(cfg.color)
        phase = math.sin(time.monotonic() * 4.0) * 0.5 + 0.5 if cfg.pulse else 0.5
        paint_pointer(painter, pos, cfg, color, phase)

    def _paint_indicator(self, painter: QPainter) -> None:
        """A small always-visible dot: the camera is on and being processed."""
        centre = QPointF(self.width() - 14, 14)
        painter.setPen(QPen(QColor(0, 0, 0, 160), 1.5))
        painter.setBrush(QColor("#34d399"))
        painter.drawEllipse(centre, 4.5, 4.5)

    def _paint_status(self, painter: QPainter) -> None:
        font = QFont()
        font.setPointSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(self._status) + 36
        pill = QRectF((self.width() - width) / 2, 14, width, 34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 18, 24, 225))
        painter.drawRoundedRect(pill, 17, 17)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, self._status)


class Overlays:
    """One overlay per monitor of the active display, so the laser can cross them.

    A single window spanning several monitors is unreliable: macOS shows a window on one
    display only, and Windows scales it by one monitor's DPI. ``set_pointer`` takes a point
    normalized to the whole (possibly combined) display and draws it on the monitor under it.
    Status text goes on the primary monitor.
    """

    def __init__(self, config: PointerConfig, display: Display) -> None:
        self._config = config
        self._display = display
        self._children: list[Overlay] = []
        self._active: Overlay | None = None
        self._shown = False
        self.latencies_ms: deque[float] = deque(maxlen=300)
        self.set_display(display)

    @property
    def can_sample(self) -> bool:
        return all(child.can_sample for child in self._children)

    def set_display(self, display: Display) -> None:
        parts = list(display.parts) or [display]
        self._display = display
        if [c._display for c in self._children] == parts:
            return
        for child in self._children:
            child.close()
            child.deleteLater()
        self._children = []
        for part in parts:
            child = Overlay(self._config, part)
            child.latencies_ms = self.latencies_ms  # one latency record for the laser
            if self._shown:
                child.show()
            self._children.append(child)
        self._active = None

    def _status_child(self) -> Overlay:
        return next((c for c in self._children if c._display.primary), self._children[0])

    def show(self) -> None:
        self._shown = True
        for child in self._children:
            child.show()

    def isVisible(self) -> bool:  # the QWidget name, so callers need not know
        return any(child.isVisible() for child in self._children)

    def update(self) -> None:
        for child in self._children:
            child.update()

    def set_pointer(
        self, point: tuple[float, float] | None, visible: bool, captured_ns: int | None = None
    ) -> None:
        if point is None or len(self._children) == 1:
            target, local = self._children[0] if point is not None else self._active, point
        else:
            part, x, y = self._display.locate(*point)
            target = next(c for c in self._children if c._display == part)
            local = (x, y)
        if self._active is not None and self._active is not target:
            self._active.set_pointer(None, False)  # the laser left that monitor
        if target is not None:
            target.set_pointer(local, visible, captured_ns)
            self._active = target

    def set_status(self, text: str, tracking_on: bool) -> None:
        status = self._status_child()
        for child in self._children:
            child.set_status(text if child is status else "", tracking_on)
