"""The calibration wizard: a fullscreen view that draws the engine's targets.

The gaze engine owns the procedure (which point, how many samples, the fit);
this window only renders the cues it emits and the result it reports.
"""

from __future__ import annotations

from fovea.events import CalibrationCue, CalibrationDone, GazeTestDone
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from superhero.platform.displays import Display


def describe_error(error: float, display: Display) -> str:
    """Human-readable accuracy for a display-normalized error."""
    pixels = error * (display.width + display.height) / 2
    text = f"{error * 100:.1f}% of the screen (about {pixels:.0f} px"
    degrees = display.error_to_degrees(error)
    return text + (f", {degrees:.1f}° at 60 cm)" if degrees is not None else ")")


def quality_label(error: float) -> str:
    if error <= 0.04:
        return "Excellent"
    if error <= 0.07:
        return "Good"
    if error <= 0.12:
        return "Usable — consider recalibrating with better lighting"
    return "Poor — recalibrate; face the camera in even light"


class CalibrationWindow(QWidget):
    cancelled = Signal()
    test_requested = Signal()
    finished = Signal()

    def __init__(self, display: Display) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._display = display
        self._cue: CalibrationCue | None = None
        self._headline = "Follow the dot with your eyes. Keep your head still."
        self._detail = "Press Esc to cancel"
        self._done = False
        self.setGeometry(display.x, display.y, display.width, display.height)

    def on_event(self, event: object) -> None:
        if isinstance(event, CalibrationCue):
            self._cue, self._done = event, False
            self._detail = event.instruction
        elif isinstance(event, CalibrationDone):
            self._cue, self._done = None, True
            self._headline = f"Calibrated — {quality_label(event.loo_error)}"
            self._detail = (
                f"Estimated error: {describe_error(event.loo_error, self._display)}\n\n"
                "Enter: finish     T: measure accuracy     Esc: close"
            )
        elif isinstance(event, GazeTestDone):
            self._cue, self._done = None, True
            self._headline = f"Measured accuracy — {quality_label(event.median_error)}"
            self._detail = (
                f"Median error: {describe_error(event.median_error, self._display)}\n"
                f"Worst point: {describe_error(event.max_error, self._display)}\n\n"
                "Enter: finish     T: measure again"
            )
        else:
            return
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            (self.finished if self._done else self.cancelled).emit()
        elif self._done and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finished.emit()
        elif self._done and key == Qt.Key.Key_T:
            self._headline = "Look at each dot again to measure accuracy."
            self._done = False
            self.test_requested.emit()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0d0f14"))
        font = QFont()
        font.setPointSize(20)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#f4f5f7"))
        centre = self.rect().adjusted(0, self.height() // 3, 0, 0)
        top = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        if self._cue is None or self._done:
            painter.drawText(centre, top, self._headline)
        font.setPointSize(12)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor("#9aa3b2"))
        painter.drawText(
            centre.adjusted(0, 56 if self._cue is None else 0, 0, 0), top, self._detail
        )

        cue = self._cue
        if cue is not None:
            pos = QPointF(cue.x * (self.width() - 1), cue.y * (self.height() - 1))
            progress = min(1.0, cue.samples / cue.needed) if cue.needed else 0.0
            painter.setPen(QPen(QColor("#22e0ff"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            ring = 34 - 22 * progress  # the ring closes in as samples accumulate
            painter.drawEllipse(pos, ring, ring)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ff2d2d"))
            painter.drawEllipse(pos, 9, 9)
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(pos, 2.5, 2.5)
            painter.setPen(QColor("#5b6472"))
            painter.drawText(
                self.rect().adjusted(0, 28, 0, 0),
                top,
                f"Point {cue.index + 1} of {cue.total}",
            )
        painter.end()
