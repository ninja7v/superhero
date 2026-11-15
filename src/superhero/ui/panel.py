"""The control panel: every setting, in plain language."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QShowEvent,
)
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from superhero import config as cfg
from superhero.interaction.commands import spec_problem
from superhero.perception.camera import list_cameras
from superhero.ui.overlay import contrast_color, paint_pointer

if TYPE_CHECKING:
    from superhero.app import Controller

# -- design tokens ----------------------------------------------------------------------

INK = "#121419"  # page
SIDEBAR = "#0c0e12"
SURFACE = "#1a1d24"  # inputs and cards
RAISED = "#232733"  # hover, selection
LINE = "#2a2f3b"
LINE_HOVER = "#3a4152"
TEXT = "#eceef2"
MUTED = "#9aa2b1"  # 7:1 on INK
FOCUS = "#c9cfda"
LASER = "#ff3b3b"  # the brand: the laser itself
LASER_BUTTON = "#d32f32"  # white text on it passes 4.5:1
LASER_BUTTON_HOVER = "#bf2629"
ON = "#3ecf8e"  # switched on, healthy
WARN = "#f5b04c"
ERROR = "#ff6b6b"
IDLE = "#5d6575"
LEVELS = {"ok": ON, "warn": WARN, "error": ERROR, "idle": IDLE}

LABEL_WIDTH = 196
FIELD_WIDTH = 340
NUMBER_WIDTH = 132
GUTTER = 24
CONTENT_WIDTH = LABEL_WIDTH + GUTTER + FIELD_WIDTH

SWATCHES = ("auto", "#ff2d2d", "#22e0ff", "#39ff88", "#ffb020", "#ff3df0")

STATUS_GROUPS = {
    "Tracking": ("Camera", "Tracking", "Pointer latency", "Calibration"),
    "Desktop": ("Desktop control", "Emergency shortcut"),
    "Live signals": ("Last gesture", "Mouth opening", "Hand"),
}

RESOLUTIONS = {
    "640x480": (640, 480),
    "1280x720": (1280, 720),
    "1920x1080": (1920, 1080),
    "2560x1440": (2560, 1440),
    "3840x2160": (3840, 2160),
}
LANGUAGES = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "": "Detect automatically",
}  # fmt: skip

_STYLE = """
* { font-size: 13px; color: @TEXT; }
QMainWindow, QWidget#page, QScrollArea, QStackedWidget { background: @INK; border: none; }
QWidget#sidebar { background: @SIDEBAR; }
QLabel#title { font-size: 22px; font-weight: 600; }
QLabel#subtitle, QLabel#rowlabel { color: @MUTED; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#hint, QLabel#rowdesc, QLabel#sidemeta { color: @MUTED; font-size: 12px; }
QLabel#herotitle { font-size: 17px; font-weight: 600; }
QLabel#preview { background: @SURFACE; border-radius: 12px; color: @MUTED; }
QLabel#empty { background: @SURFACE; border: 1px dashed @LINE; border-radius: 10px;
  color: @MUTED; padding: 18px; }
QFrame#card { background: @SURFACE; border-radius: 12px; }

QListWidget#nav { background: transparent; border: none; outline: none; }
QListWidget#nav::item { color: @MUTED; padding: 8px 12px; border-radius: 8px; margin: 1px 0; }
QListWidget#nav::item:hover { background: @SURFACE; color: @TEXT; }
QListWidget#nav::item:selected { background: @RAISED; color: @TEXT; }

QPushButton { background: @SURFACE; border: 1px solid @LINE; border-radius: 8px;
  padding: 7px 14px; }
QPushButton:hover { background: @RAISED; border-color: @LINE_HOVER; }
QPushButton:pressed { background: @LINE; }
QPushButton:focus { border-color: @FOCUS; }
QPushButton:disabled { color: @IDLE; background: @INK; }
QPushButton#primary { background: @LASER_BUTTON; border-color: @LASER_BUTTON; color: #ffffff;
  font-weight: 600; }
QPushButton#primary:hover { background: @LASER_BUTTON_HOVER; border-color: @LASER_BUTTON_HOVER; }
QPushButton#primary:focus { border-color: #ffffff; }
QPushButton#danger { color: @ERROR; }
QPushButton#chip { padding: 6px 14px; }
QPushButton#chip:checked { background: @RAISED; border-color: @MUTED; }

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: @SURFACE; border: 1px solid @LINE;
  border-radius: 8px; padding: 6px 10px; selection-background-color: @RAISED; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: @LINE_HOVER; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: @FOCUS; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: url(@CHEVRON); width: 12px; height: 12px; }
QAbstractSpinBox { padding-right: 24px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { subcontrol-origin: border;
  width: 22px; border: none; background: transparent; }
QAbstractSpinBox::up-button { subcontrol-position: top right; margin-top: 3px; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right; margin-bottom: 3px; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background: @RAISED;
  border-radius: 4px; }
QAbstractSpinBox::up-arrow { image: url(@UP); width: 10px; height: 10px; }
QAbstractSpinBox::down-arrow { image: url(@CHEVRON); width: 10px; height: 10px; }
QComboBox QAbstractItemView { background: @SURFACE; border: 1px solid @LINE; outline: none;
  padding: 4px; selection-background-color: @RAISED; selection-color: @TEXT; }

QSlider { min-height: 22px; background: transparent; }
QSlider::groove:horizontal { height: 4px; background: @LINE; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @MUTED; border-radius: 2px; }
QSlider::add-page:horizontal { background: @LINE; border-radius: 2px; }
QSlider::handle:horizontal { background: @TEXT; width: 16px; height: 16px; margin: -6px 0;
  border-radius: 8px; }
QSlider::handle:horizontal:hover { background: #ffffff; }
QSlider::handle:horizontal:focus { background: #ffffff; border: 2px solid @FOCUS; }

QTableWidget { background: @SURFACE; border: 1px solid @LINE; border-radius: 10px;
  gridline-color: @LINE; selection-background-color: @RAISED; selection-color: @TEXT; }
QHeaderView::section { background: @SURFACE; color: @MUTED; border: none;
  border-bottom: 1px solid @LINE; padding: 7px 10px; }
QTableCornerButton::section { background: @SURFACE; border: none; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @LINE; border-radius: 3px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: @LINE_HOVER; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QToolTip { background: @RAISED; color: @TEXT; border: 1px solid @LINE; padding: 6px; }
QMessageBox, QInputDialog { background: @INK; }
"""


def stylesheet() -> str:
    tokens = {
        "LASER_BUTTON_HOVER": LASER_BUTTON_HOVER, "LASER_BUTTON": LASER_BUTTON,
        "LINE_HOVER": LINE_HOVER, "SURFACE": SURFACE, "SIDEBAR": SIDEBAR, "RAISED": RAISED,
        "MUTED": MUTED, "FOCUS": FOCUS, "ERROR": ERROR, "TEXT": TEXT, "LINE": LINE,
        "IDLE": IDLE, "INK": INK,
        "CHEVRON": (Path(__file__).parent / "assets" / "chevron-down.svg").as_posix(),
        "UP": (Path(__file__).parent / "assets" / "chevron-up.svg").as_posix(),
    }  # fmt: skip
    style = _STYLE
    for name, value in tokens.items():  # longest names first, so LINE never eats LINE_HOVER
        style = style.replace(f"@{name}", value)
    return style


def _tabular(widget: QWidget) -> None:
    """Fixed-width digits, so live numbers do not jitter as they change."""
    font = widget.font()
    try:
        font.setFeature(QFont.Tag("tnum"), 1)
    except (AttributeError, TypeError):  # Qt < 6.7
        return
    widget.setFont(font)


def _fit_height(label: QLabel, width: int) -> None:
    """Wrapped labels nested in a form row do not report their height; tell the layout."""
    label.setMinimumHeight(label.heightForWidth(width))


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * t,
        a.greenF() + (b.greenF() - a.greenF()) * t,
        a.blueF() + (b.blueF() - a.blueF()) * t,
    )


# -- small widgets ------------------------------------------------------------------------


class Dot(QWidget):
    """A status light. Colour carries the level; the text beside it carries the meaning."""

    def __init__(self, size: int = 8, level: str = "idle") -> None:
        super().__init__()
        self._color = QColor(LEVELS[level])
        self.setFixedSize(size, size)

    def set_level(self, level: str) -> None:
        color = QColor(LEVELS.get(level, IDLE))
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(self.rect()))


class Switch(QAbstractButton):
    """An on/off switch. Space toggles it; the knob slides in 150 ms."""

    def __init__(self, checked: bool, name: str) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._position = 1.0 if checked else 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._slide)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(40, 24)

    def _animate(self, on: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if on else 0.0)
        self._animation.start()

    def _slide(self, value: object) -> None:
        self._position = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(2, 2, 36, 20)
        if self.hasFocus():
            painter.setPen(QPen(QColor(FOCUS), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-2, -2, 2, 2), 12, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_mix(QColor(LINE_HOVER), QColor(ON), self._position))
        painter.drawRoundedRect(track, 10, 10)
        knob_x = track.left() + 2 + self._position * (track.width() - 20)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(knob_x, track.top() + 2, 16, 16))


class ToggleRow(QWidget):
    """Title and explanation on the left, a switch on the right. Clicking the text toggles too."""

    def __init__(self, title: str, description: str, checked: bool) -> None:
        super().__init__()
        self.switch = Switch(checked, title)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(QLabel(title))
        if description:
            text.addWidget(QLabel(description, objectName="rowdesc", wordWrap=True))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 6, 0, 6)
        row.setSpacing(GUTTER)
        row.addLayout(text, 1)
        row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setMaximumWidth(CONTENT_WIDTH)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.switch.toggle()
            self.switch.setFocus()


class Swatch(QAbstractButton):
    """One pointer colour. ``auto`` is drawn half red, half cyan."""

    def __init__(self, color: str, name: str) -> None:
        super().__init__()
        self.color = color
        self.setCheckable(True)
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        disc = QRectF(5, 5, 20, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.color == "custom":
            painter.setPen(QPen(QColor(MUTED), 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(disc.adjusted(0.7, 0.7, -0.7, -0.7))
            centre = disc.center()
            painter.drawLine(centre + QPointF(-4.5, 0), centre + QPointF(4.5, 0))
            painter.drawLine(centre + QPointF(0, -4.5), centre + QPointF(0, 4.5))
        elif self.color == "auto":
            painter.setBrush(QColor("#ff2d2d"))
            painter.drawPie(disc, 90 * 16, 180 * 16)
            painter.setBrush(QColor("#22e0ff"))
            painter.drawPie(disc, 270 * 16, 180 * 16)
        else:
            painter.setBrush(QColor(self.color))
            painter.drawEllipse(disc)
        if self.isChecked() or self.hasFocus():
            painter.setPen(QPen(QColor(TEXT if self.isChecked() else FOCUS), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(1.5, 1.5, 27, 27))


class PointerPreview(QWidget):
    """The real laser, drawn on a light and a dark background, exactly as the overlay draws it."""

    def __init__(self, config: cfg.PointerConfig) -> None:
        super().__init__()
        self._config = config
        self._phase = 0.0
        self.setFixedHeight(116)
        self.setMaximumWidth(CONTENT_WIDTH)
        self.setAccessibleName("Laser pointer preview")
        self._timer = QTimer(self, interval=33)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.033 * 4.0) % (2 * 3.14159265)
        self.update()

    def showEvent(self, _event: object) -> None:
        self._timer.start()

    def hideEvent(self, _event: object) -> None:
        self._timer.stop()

    def paintEvent(self, _event: QPaintEvent) -> None:
        import math

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 12, 12)
        painter.setClipPath(clip)
        half = bounds.width() / 2
        phase = math.sin(self._phase) * 0.5 + 0.5 if self._config.pulse else 0.5
        for index, (background, luminance, caption) in enumerate(
            (("#eef0f3", 0.94, "#5b6270"), ("#0b0c10", 0.04, "#8e96a6"))
        ):
            area = QRectF(bounds.left() + index * half, bounds.top(), half, bounds.height())
            painter.fillRect(area, QColor(background))
            painter.setPen(QColor(caption))
            painter.drawText(
                area.adjusted(14, 10, -14, -10),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                "On a light page" if index == 0 else "On a dark page",
            )
            if self._config.color == "auto":
                color = contrast_color(luminance)
            else:
                color = QColor(self._config.color)
            paint_pointer(painter, area.center() + QPointF(0, 8), self._config, color, phase)


class WheelGuard(QObject):
    """Scrolling the page over a dropdown or slider scrolls the page; it does not change values.

    A control only takes the wheel once it has been clicked or tabbed into.
    """

    def __init__(self, area: QScrollArea) -> None:
        super().__init__(area)
        self._area = area

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and not watched.hasFocus():  # type: ignore[attr-defined]
            QCoreApplication.sendEvent(self._area.verticalScrollBar(), event)
            return True
        return False


class BrandMark(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(14, 14)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1.2))
        painter.setBrush(QColor(LASER))
        painter.drawEllipse(QRectF(1.5, 1.5, 11, 11))


# -- the panel ----------------------------------------------------------------------------


class Panel(QMainWindow):
    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.c = controller
        self.setWindowTitle("SUPERHERO")
        self.resize(920, 660)
        self.setMinimumSize(820, 560)
        self.setStyleSheet(stylesheet())

        sidebar = QWidget(objectName="sidebar")
        sidebar.setFixedWidth(196)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 20, 14, 16)
        side.setSpacing(0)
        brand = QHBoxLayout()
        brand.setContentsMargins(10, 0, 0, 0)
        brand.setSpacing(10)
        brand.addWidget(BrandMark())
        wordmark = QLabel("SUPERHERO")
        font = wordmark.font()
        font.setWeight(QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        wordmark.setFont(font)
        brand.addWidget(wordmark)
        brand.addStretch(1)
        side.addLayout(brand)
        side.addSpacing(22)
        self._nav = QListWidget(objectName="nav")
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side.addWidget(self._nav, 1)
        status = QHBoxLayout()
        status.setContentsMargins(10, 0, 0, 0)
        status.setSpacing(10)
        self._side_dot = Dot(8)
        status.addWidget(self._side_dot, 0, Qt.AlignmentFlag.AlignTop)
        lines = QVBoxLayout()
        lines.setSpacing(1)
        self._side_state = QLabel("Starting…")
        self._side_meta = QLabel("", objectName="sidemeta")
        _tabular(self._side_meta)
        lines.addWidget(self._side_state)
        lines.addWidget(self._side_meta)
        status.addLayout(lines, 1)
        side.addLayout(status)

        self._stack = QStackedWidget()
        self._nav.currentRowChanged.connect(self._on_page)
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(root)
        self._build()

    # -- page scaffolding --------------------------------------------------------------

    def _build(self) -> None:
        row = max(self._nav.currentRow(), 0)
        self._nav.blockSignals(True)
        self._nav.clear()
        while self._stack.count():
            old = self._stack.widget(0)
            self._stack.removeWidget(old)
            old.deleteLater()
        self._status_rows: dict[str, tuple[Dot, QLabel]] = {}
        self._requirement_notes: list[tuple[QLabel, Callable[[], str]]] = []
        self.pointer_preview: PointerPreview | None = None
        for name, subtitle, builder in (
            ("Home", "What SUPERHERO is doing right now.", self._home),
            ("Camera", "Which camera SUPERHERO watches, and what it sees.", self._camera),
            ("Tracking", "Which parts of you SUPERHERO follows.", self._tracking),
            ("Calibration", "Teach the laser where you are looking.", self._calibration),
            ("Gestures", "What each gesture does.", self._gestures),
            ("Laser pointer", "How the laser looks and moves.", self._pointer),
            ("Voice", "Dictation and spoken commands.", self._voice),
            ("AI commands", "Requests in your own words, planned by a local model.", self._ai),
            ("Profiles", "Save, switch and share your settings.", self._profiles),
        ):
            self._nav.addItem(name)
            area = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
            self._guard = WheelGuard(area)
            page = QWidget(objectName="page")
            self._column = QVBoxLayout(page)
            self._column.setContentsMargins(40, 30, 40, 40)
            self._column.setSpacing(0)
            self._column.addWidget(QLabel(name, objectName="title"))
            self._column.addSpacing(4)
            self._column.addWidget(QLabel(subtitle, objectName="subtitle"))
            self._column.addSpacing(14)
            self._form: QFormLayout | None = None
            builder()
            self._column.addStretch(1)
            area.setWidget(page)
            self._stack.addWidget(area)
        self._nav.blockSignals(False)
        self._nav.setCurrentRow(row)
        self._stack.setCurrentIndex(row)

    def _section(self, title: str, description: str = "") -> QLabel | None:
        self._column.addSpacing(22)
        self._column.addWidget(QLabel(title, objectName="section"))
        note = None
        if description:
            self._column.addSpacing(3)
            note = QLabel(description, objectName="hint", wordWrap=True)
            note.setMaximumWidth(CONTENT_WIDTH)
            self._column.addWidget(note)
        self._column.addSpacing(10)
        self._form = None
        return note

    def _field(self, label: str, widget: QWidget, width: int = FIELD_WIDTH) -> QWidget:
        if self._form is None:
            self._form = QFormLayout()
            self._form.setContentsMargins(0, 0, 0, 0)
            self._form.setHorizontalSpacing(GUTTER)
            self._form.setVerticalSpacing(10)
            self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            self._column.addLayout(self._form)
        caption = QLabel(label, objectName="rowlabel", wordWrap=True)
        caption.setFixedWidth(LABEL_WIDTH)
        widget.setMaximumWidth(width)
        if isinstance(widget, (QComboBox, QSpinBox, QDoubleSpinBox, QSlider)):
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            widget.installEventFilter(self._guard)
            widget.setAccessibleName(label)
        self._form.addRow(caption, widget)
        return widget

    def _wide(self, widget: QWidget, space_before: int = 0) -> QWidget:
        if space_before:
            self._column.addSpacing(space_before)
        widget.setMaximumWidth(CONTENT_WIDTH)
        self._column.addWidget(widget)
        self._form = None
        return widget

    def _hint(self, text: str, selectable: bool = False) -> QLabel:
        label = QLabel(text, objectName="hint", wordWrap=True)
        if selectable:
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._wide(label, space_before=6)
        self._column.addSpacing(10)
        return label

    def _actions(self, *buttons: tuple[str, Callable[[], None], str]) -> list[QPushButton]:
        """A row of natural-width buttons in the field column. Kinds: primary, danger, ''."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(8)
        made = []
        for text, slot, kind in buttons:
            button = QPushButton(text, objectName=kind)
            button.clicked.connect(lambda _checked=False, s=slot: s())
            layout.addWidget(button)
            made.append(button)
        layout.addStretch(1)
        self._field("", row)
        return made

    def _set(self, obj: Any, attr: str, value: Any, restart: bool = False) -> None:
        setattr(obj, attr, value)
        if self.pointer_preview is not None:
            self.pointer_preview.update()
        self.c.settings_changed(restart)

    # -- controls ------------------------------------------------------------------------

    def _toggle(
        self, obj: Any, attr: str, title: str, description: str = "", restart: bool = False
    ) -> Switch:
        row = ToggleRow(title, description, getattr(obj, attr))
        row.switch.toggled.connect(lambda value: self._set(obj, attr, value, restart))
        self._wide(row)
        self._form = None
        return row.switch

    def _number(
        self, obj: Any, attr: str, label: str, step: float = 1, suffix: str = "",
        restart: bool = False,
    ) -> None:  # fmt: skip
        value = getattr(obj, attr)
        lo, hi = cfg.RANGES[type(obj)][attr]
        box = QSpinBox() if isinstance(value, int) else QDoubleSpinBox()
        if isinstance(box, QDoubleSpinBox):
            box.setDecimals(2)
        box.setRange(lo, hi)
        box.setSingleStep(step)
        box.setSuffix(suffix)
        box.setValue(value)
        _tabular(box)
        box.valueChanged.connect(lambda v: self._set(obj, attr, type(value)(v), restart))
        self._field(label, box, NUMBER_WIDTH)

    def _slider(
        self, obj: Any, attr: str, label: str, step: float, show: Callable[[float], str],
        restart: bool = False,
    ) -> None:  # fmt: skip
        value = getattr(obj, attr)
        lo, hi = cfg.RANGES[type(obj)][attr]
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(round(lo / step), round(hi / step))
        slider.setValue(round(value / step))
        readout = QLabel(show(value))
        readout.setFixedWidth(56)
        readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _tabular(readout)

        def moved(position: int) -> None:
            new = type(value)(round(position * step, 6))
            readout.setText(show(new))
            self._set(obj, attr, new, restart)

        slider.valueChanged.connect(moved)
        layout.addWidget(slider, 1)
        layout.addWidget(readout)
        self._field(label, row)
        slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        slider.installEventFilter(self._guard)
        slider.setAccessibleName(label)

    def _choice(
        self, label: str, options: dict[Any, str], current: Any, on_pick: Callable[[Any], None]
    ) -> QComboBox:
        box = QComboBox()
        for value, text in options.items():
            box.addItem(text, value)
        box.setCurrentIndex(max(0, box.findData(current)))
        box.activated.connect(lambda _i: on_pick(box.currentData()))
        self._field(label, box)
        return box

    def _chips(
        self, label: str, options: dict[str, str], current: str, on_pick: Callable[[str], None]
    ) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        group = QButtonGroup(row)
        for value, text in options.items():
            chip = QPushButton(text, objectName="chip", checkable=True, checked=value == current)
            chip.clicked.connect(lambda _c=False, v=value: on_pick(v))
            group.addButton(chip)
            layout.addWidget(chip)
        layout.addStretch(1)
        self._field(label, row)

    def _status_line(self) -> tuple[Dot, QLabel, QWidget]:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        dot = Dot(8)
        text = QLabel(wordWrap=True)
        _tabular(text)
        dot_column = QVBoxLayout()
        dot_column.setContentsMargins(0, 5, 0, 0)
        dot_column.addWidget(dot)
        dot_column.addStretch(1)
        layout.addLayout(dot_column)
        layout.addWidget(text, 1)
        return dot, text, row

    # -- pages ---------------------------------------------------------------------------

    def _home(self) -> None:
        hero = QFrame(objectName="card")
        layout = QHBoxLayout(hero)
        layout.setContentsMargins(20, 18, 18, 18)
        layout.setSpacing(14)
        self._hero_dot = Dot(10)
        dot_column = QVBoxLayout()
        dot_column.setContentsMargins(0, 8, 0, 0)
        dot_column.addWidget(self._hero_dot)
        dot_column.addStretch(1)
        layout.addLayout(dot_column)
        text = QVBoxLayout()
        text.setSpacing(3)
        self._hero_title = QLabel("Control is off", objectName="herotitle")
        self._hero_detail = QLabel("", objectName="subtitle", wordWrap=True)
        text.addWidget(self._hero_title)
        text.addWidget(self._hero_detail)
        layout.addLayout(text, 1)
        self.arm_button = QPushButton("Start control", objectName="primary", checkable=True)
        self.arm_button.setMinimumWidth(132)
        self.arm_button.clicked.connect(lambda on: self.c.set_armed(on))
        layout.addWidget(self.arm_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._wide(hero, space_before=8)

        for group, labels in STATUS_GROUPS.items():
            self._section(group)
            for label in labels:
                dot, text_label, row = self._status_line()
                self._status_rows[label] = (dot, text_label)
                self._field(label, row)
        hotkey = self.c.config.emergency_hotkey.replace("+", " + ").title()
        where = (
            "the tray icon, or the button above" if self.c.tray_available else "the button above"
        )
        closing = (
            "Closing this window keeps SUPERHERO running in the tray."
            if self.c.tray_available
            else "This desktop has no system tray, so closing this window quits SUPERHERO."
        )
        self._hint(
            f"Emergency stop: {hotkey}, {where}. Your mouse and keyboard always keep working. "
            f"{closing}",
        ).setContentsMargins(0, 14, 0, 0)

    def _camera(self) -> None:
        cam = self.c.config.camera
        self.preview = QLabel("Waiting for the camera…", objectName="preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(round(CONTENT_WIDTH * 9 / 16))
        self.preview.setAccessibleName("Camera preview")
        self._wide(self.preview, space_before=8)
        self._camera_dot, self.camera_status, row = self._status_line()
        self._wide(row, space_before=10)

        self._section("Source")
        cameras = {c.index: c.name.strip() for c in list_cameras()} or {
            cam.index: "No camera detected"
        }
        self._choice("Camera", cameras, cam.index, lambda v: self._set(cam, "index", v, True))

        def pick(size: tuple[int, int]) -> None:
            cam.width, cam.height = size
            self.c.settings_changed(True)

        current = RESOLUTIONS.get(f"{cam.width}x{cam.height}")
        self._choice("Resolution", {v: k for k, v in RESOLUTIONS.items()}, current, pick)
        self._number(cam, "fps", "Frame rate", 5, " fps", restart=True)
        self._toggle(
            cam, "mirror", "Mirror the image", "Like a mirror: your left is on the left.", True
        )
        self._hint(
            "1280x720 tracks the eyes more accurately than 640x480. Higher sizes help only if "
            "you sit far from the camera: the eye model sees the face at a fixed 256x256, and "
            "every extra pixel costs time. A camera that cannot deliver a size uses the "
            "nearest one it has (640x480 if HD comes too slowly); the status above shows the "
            "size in use. Each size has its own calibration. The preview is shown only on "
            "this page and is never recorded."
        )

    def _tracking(self) -> None:
        tracking = self.c.config.tracking
        self._toggle(tracking, "eye", "Eye tracking", "The laser follows your gaze.")
        self._toggle(
            tracking, "mouth", "Mouth tracking",
            "Hold your mouth open as a gesture — by default it starts voice mode.", True,
        )  # fmt: skip
        self._toggle(
            tracking, "head", "Head tracking", "Tilt your head to scroll, nod to confirm.", True
        )
        self._toggle(
            tracking, "hand", "Hand tracking",
            "Pinch, two fingers, open palm and fist. Runs a second model on every frame, so it "
            "uses noticeably more processor.", True,
        )  # fmt: skip
        self._hint("Pen tracking is an experiment and not available here yet.")

    def _calibration(self) -> None:
        config = self.c.config
        displays = {
            d.name: f"{d.name}  ·  {d.width}×{d.height}".strip(" ·")  # noqa: RUF001 - a real multiplication sign
            for d in self.c.displays
        }

        def pick_display(name: str) -> None:
            config.active_display = name
            self.c.settings_changed(True)

        self._section("Setup")
        self._choice("Display", displays, self.c.display.name, pick_display)
        self._choice(
            "Points",
            {5: "5 — quickest", 9: "9 — recommended", 13: "13 — most accurate"},
            config.calibration_points,
            lambda v: self._set(config, "calibration_points", v),
        )
        self._actions(
            ("Calibrate now", self.c.start_calibration, "primary"),
            ("Measure accuracy", self.c.start_accuracy_test, ""),
        )
        self._section("Result")
        card = QFrame(objectName="card")
        inner = QHBoxLayout(card)
        inner.setContentsMargins(16, 14, 16, 14)
        self._calibration_dot, self.calibration_status, row = self._status_line()
        inner.addWidget(row)
        self._wide(card)
        self._hint(
            "Each display and camera has its own calibration. Recalibrate after moving the "
            "camera, the display, or your seat."
        )

    def _gestures(self) -> None:
        gestures, tracking = self.c.config.gestures, self.c.config.tracking

        def binding(name: str) -> None:
            self._choice(
                cfg.GESTURES[name],
                cfg.ACTIONS,
                gestures.bindings.get(name, "none"),
                lambda action: (
                    gestures.bindings.__setitem__(name, action),
                    self.c.settings_changed(False),
                ),
            )

        def requirement(note: QLabel | None, text: Callable[[], str]) -> None:
            if note is not None:
                self._requirement_notes.append((note, text))
                note.setText(text())

        self._section("Eyes")
        for name in ("wink_left", "wink_right", "double_blink", "long_blink", "dwell"):
            binding(name)
        note = self._section("Face", " ")
        requirement(
            note,
            lambda: (
                " ".join(
                    part
                    for part in (
                        ""
                        if tracking.mouth
                        else "Mouth tracking is off, so the mouth gesture does nothing.",
                        ""
                        if tracking.head
                        else "Head tracking is off, so head gestures do nothing.",
                    )
                    if part
                )
                or "Mouth and head tracking are on."
            ),
        )
        for name in ("mouth_open", "head_tilt_left", "head_tilt_right", "head_nod"):
            binding(name)
        note = self._section("Hands", " ")
        requirement(
            note,
            lambda: (
                "Hand tracking is on."
                if tracking.hand
                else "Hand tracking is off, so these do nothing. Turn it on under Tracking."
            ),
        )
        for name in ("hand_pinch", "hand_two_fingers", "hand_open_palm", "hand_fist"):
            binding(name)

        self._section("Timing")
        self._number(gestures, "cooldown_ms", "Pause between actions", 50, " ms")
        self._number(gestures, "dwell_ms", "Dwell time", 50, " ms")
        self._number(gestures, "wink_min_ms", "Shortest wink", 10, " ms", True)
        self._number(gestures, "wink_max_ms", "Longest wink", 50, " ms", True)
        self._number(gestures, "double_blink_ms", "Double-blink window", 50, " ms", True)
        self._number(gestures, "long_blink_ms", "Long blink", 50, " ms", True)
        self._number(gestures, "mouth_open_ms", "Mouth held open for", 100, " ms", True)
        self._number(gestures, "hand_hold_ms", "Hand pose held for", 50, " ms", True)

        self._section("Sensitivity", "The Home page shows your live mouth opening to compare with.")
        self._slider(
            gestures, "mouth_open_threshold", "Mouth opening", 0.05,
            lambda v: f"{v:.2f}", True,
        )  # fmt: skip
        self._number(gestures, "head_tilt_deg", "Head tilt angle", 1, "°", True)
        self._number(gestures, "head_nod_deg", "Head nod angle", 1, "°", True)
        self._number(gestures, "scroll_lines", "Scroll step", 1, " lines")

    def _pointer(self) -> None:
        pointer = self.c.config.pointer
        self.pointer_preview = PointerPreview(pointer)
        self._wide(self.pointer_preview, space_before=8)

        self._section("Look")
        self._slider(pointer, "size", "Size", 1, lambda v: f"{v:.0f} px")
        self._chips(
            "Style",
            {"dot": "Laser dot", "ring": "Ring", "crosshair": "Crosshair"},
            pointer.style,
            lambda v: self._set(pointer, "style", v),
        )
        swatches = QWidget()
        row = QHBoxLayout(swatches)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        group = QButtonGroup(swatches)
        names = {"auto": "Adapts to the background", "#ff2d2d": "Red", "#22e0ff": "Cyan",
                 "#39ff88": "Green", "#ffb020": "Amber", "#ff3df0": "Magenta"}  # fmt: skip
        current = pointer.color.lower()
        for color in SWATCHES:
            swatch = Swatch(color, names[color])
            swatch.setChecked(color == current)
            swatch.clicked.connect(lambda _c=False, v=color: self._set(pointer, "color", v))
            group.addButton(swatch)
            row.addWidget(swatch)
        custom = Swatch(current if current not in SWATCHES else "custom", "Custom colour…")
        custom.setChecked(current not in SWATCHES)
        group.addButton(custom)

        def pick_custom() -> None:
            start = QColor(pointer.color if pointer.color != "auto" else "#ff2d2d")
            colour = QColorDialog.getColor(start, self, "Pointer colour")
            if colour.isValid():
                custom.color = colour.name()
                self._set(pointer, "color", colour.name())
            else:  # put the selection back where it was
                for button in group.buttons():
                    button.setChecked(getattr(button, "color", "") == pointer.color.lower())
            custom.update()

        custom.clicked.connect(pick_custom)
        row.addSpacing(8)
        row.addWidget(custom)
        row.addStretch(1)
        self._field("Colour", swatches)
        if not self.c.overlay.can_sample:
            self._hint(
                "This desktop does not let apps read the screen, so “adapts to the background” "
                "shows red. The double outline keeps it visible on any background."
            )
        self._toggle(pointer, "pulse", "Pulse", "A slow glow, so the laser is easy to find.")

        self._section("Movement")
        self._slider(
            pointer, "smoothing_mincutoff", "Steadiness", 0.1,
            lambda v: f"{v:.1f}", True,
        )  # fmt: skip
        self._hint("Lower is steadier when you hold your gaze, but slower to follow.")
        self._slider(
            pointer, "smoothing_beta", "Speed on fast moves", 0.01,
            lambda v: f"{v:.2f}", True,
        )  # fmt: skip
        self._slider(
            pointer, "min_confidence", "Hide below confidence", 0.05,
            lambda v: f"{v:.0%}",
        )  # fmt: skip
        self._toggle(
            pointer, "move_system_cursor", "Move the mouse cursor too",
            "The system cursor follows your gaze as well as the laser.",
        )  # fmt: skip

    def _voice(self) -> None:
        voice = self.c.config.voice
        self._section("Recognition")
        self._choice(
            "Mode",
            {
                "auto": "Commands, otherwise dictation",
                "commands": "Commands only",
                "dictation": "Dictation only",
            },
            voice.mode,
            lambda v: self._set(voice, "mode", v),
        )
        self._choice(
            "Language", LANGUAGES, voice.language, lambda v: self._set(voice, "language", v)
        )
        self._choice(
            "Recognition model",
            {
                "tiny": "Tiny — fastest",
                "base": "Base — balanced",
                "small": "Small — more accurate",
                "medium": "Medium — slow on this kind of processor",
            },
            voice.model,
            lambda v: (self._set(voice, "model", v), self.c.rebuild_voice()),
        )
        mics = {"": "System default"} | {
            d.description(): d.description() for d in QMediaDevices.audioInputs()
        }
        self._choice(
            "Microphone", mics, voice.microphone, lambda v: self._set(voice, "microphone", v)
        )
        self._hint(
            "Speech is recognised on this computer. The model is downloaded once on first use; "
            "no audio is uploaded."
        )

        self._section("Listening")
        self._number(voice, "silence_timeout_s", "Stop after a silence of", 0.5, " s")
        self._number(voice, "max_seconds", "Longest recording", 1, " s")
        self._number(voice, "prebuffer_ms", "Keep audio from before", 100, " ms")

        self._section(
            "Your commands",
            "Map a phrase to keys (key:ctrl+alt+t), text (type:Kind regards), an app "
            "(launch:firefox) or an action (action:left_click).",
        )
        self._commands_empty = QLabel(
            "No commands yet. For example, “open terminal” → key:ctrl+alt+t.",
            objectName="empty",
            wordWrap=True,
        )
        self._wide(self._commands_empty)
        table = self.commands_table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["When I say", "Do"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setShowGrid(False)
        table.setMinimumHeight(170)
        for phrase, spec in voice.custom_commands.items():
            self._add_command_row(phrase, spec)
        table.itemChanged.connect(lambda _item: self._save_commands())
        self._wide(table)
        self._column.addSpacing(10)
        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for text, slot in (
            ("Add a command", self._new_command),
            ("Remove selected", self._remove_commands),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _c=False, s=slot: s())
            row.addWidget(button)
        row.addStretch(1)
        self._wide(buttons)
        self._sync_commands_empty()

    def _ai(self) -> None:
        from superhero.ai.planner import is_local, list_models

        ai = self.c.config.ai
        self._toggle(
            ai, "enabled", "AI commands",
            "Let a model work out requests the built-in commands do not cover.",
        )  # fmt: skip
        self._section("Model")
        models = list_models(ai)
        if models and not ai.model:
            ai.model = next((m for m in models if m.startswith("gemma")), models[0])
        options = {m: m for m in models} or {
            ai.model: ai.model or "No model found — is Ollama running?"
        }
        self._choice("Model", options, ai.model, lambda v: self._set(ai, "model", v))
        where = "on this computer" if is_local(ai.url) else "on another machine"
        server = QLabel(f"{ai.url}  ·  {where}")
        server.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._field("Server", server)

        self._section("How to use it")
        self._hint(
            f"In voice mode, say “{ai.wake_word}, …” — for example “{ai.wake_word}, open "
            "Firefox and search for the weather in Paris”. The model proposes a few keystrokes "
            "and clicks; they appear at the top of the screen and nothing happens until you wink "
            "or say “yes”. It cannot see your screen or read your files, and it takes several "
            "seconds."
        )
        self._hint(
            "The model runs locally through Ollama, so requests stay on this computer and cost "
            "nothing. Cloud models are not included."
        )

    def _profiles(self) -> None:
        self._section("Profile")
        self._choice(
            "Active profile",
            {name: name for name in cfg.list_profiles()},
            self.c.profile,
            self.c.load_profile,
        )

        def save_as() -> None:
            name, ok = QInputDialog.getText(self, "Save profile", "Profile name")
            if ok and name.strip() and Path(name.strip()).name == name.strip():
                self.c.save_profile_as(name.strip())

        def export() -> None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export settings", "superhero-settings.json", "JSON (*.json)"
            )
            if path:
                cfg.save_file(self.c.config, Path(path))

        def import_() -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "JSON (*.json)")
            if not path:
                return
            try:  # parse first: a wrong file must not replace the current settings
                imported = cfg.read_file(Path(path))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(
                    self,
                    "Import settings",
                    f"Nothing was changed. That file cannot be used:\n{exc}",
                )
                return
            cfg.save(imported, self.c.profile)
            self.c.load_profile(self.c.profile)

        self._actions(
            ("Save as new…", save_as, ""),
            ("Export…", export, ""),
            ("Import…", import_, ""),
        )
        self._hint(f"Profiles are stored in {cfg.config_dir() / 'profiles'}", selectable=True)

        self._section(
            "Start over",
            "Puts every setting in this profile back to its default. Calibrations are kept.",
        )

        def reset() -> None:
            answer = QMessageBox.question(
                self,
                "Reset to defaults",
                f"Reset every setting in the “{self.c.profile}” profile? This cannot be undone.",
                QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Reset:
                self.c.reset_profile()

        self._actions(("Reset to defaults…", reset, "danger"))

    # -- voice commands table ------------------------------------------------------------

    def _add_command_row(self, phrase: str, spec: str) -> int:
        table = self.commands_table
        table.blockSignals(True)
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(phrase))
        table.setItem(row, 1, QTableWidgetItem(spec))
        self._mark_spec(table.item(row, 1))
        table.blockSignals(False)
        return row

    @staticmethod
    def _mark_spec(item: QTableWidgetItem | None) -> None:
        """Show, on the cell itself, why a command would not run."""
        if item is None:
            return
        problem = spec_problem(item.text()) if item.text().strip() else ""
        item.setForeground(QColor(ERROR if problem else TEXT))
        item.setToolTip(f"This command will not run: {problem}" if problem else "")

    def _new_command(self) -> None:
        row = self._add_command_row("", "")
        self._sync_commands_empty()
        self.commands_table.setCurrentCell(row, 0)
        self.commands_table.editItem(self.commands_table.item(row, 0))

    def _remove_commands(self) -> None:
        table = self.commands_table
        for row in sorted({index.row() for index in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)
        self._save_commands()
        self._sync_commands_empty()

    def _sync_commands_empty(self) -> None:
        empty = self.commands_table.rowCount() == 0
        self._commands_empty.setVisible(empty)
        self.commands_table.setVisible(not empty)

    def _save_commands(self) -> None:
        # Rows that do not parse are kept (and marked) rather than dropped, so a typo is
        # fixed in place instead of disappearing; spoken, they report what is wrong.
        table, commands = self.commands_table, {}
        table.blockSignals(True)
        for row in range(table.rowCount()):
            phrase, spec = table.item(row, 0), table.item(row, 1)
            self._mark_spec(spec)
            if phrase and spec and phrase.text().strip() and spec.text().strip():
                commands[phrase.text().strip()] = spec.text().strip()
        table.blockSignals(False)
        self.c.config.voice.custom_commands = commands
        self.c.settings_changed(False)

    # -- live updates --------------------------------------------------------------------

    def rebuild(self) -> None:
        self._build()

    def _on_page(self, row: int) -> None:
        self._stack.setCurrentIndex(row)
        self.c.set_preview(self.isVisible() and row == 1)
        for note, text in self._requirement_notes:
            note.setText(text())

    def show_frame(self, image: QImage) -> None:
        self.preview.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def refresh(
        self,
        armed: bool,
        rows: Sequence[tuple[str, str, str]],
        calibration: tuple[str, str],
        hero_detail: str,
    ) -> None:
        """``rows`` are (label, text, level); level is ok, warn, error or idle."""
        self.arm_button.setChecked(armed)
        self.arm_button.setText("Pause control" if armed else "Start control")
        kind = "" if armed else "primary"
        if self.arm_button.objectName() != kind:
            self.arm_button.setObjectName(kind)
            self.arm_button.style().unpolish(self.arm_button)
            self.arm_button.style().polish(self.arm_button)
        self._hero_dot.set_level("ok" if armed else "idle")
        self._hero_title.setText("Control is on" if armed else "Control is off")
        self._hero_detail.setText(hero_detail)

        by_label = {label: (text, level) for label, text, level in rows}
        for label, (dot, text_label) in self._status_rows.items():
            text, level = by_label.get(label, ("—", "idle"))
            if text_label.text() != text:
                text_label.setText(text)
                _fit_height(text_label, FIELD_WIDTH - 18)
            dot.set_level(level)

        camera_text, camera_level = by_label.get("Camera", ("", "idle"))
        self.camera_status.setText(camera_text)
        self._camera_dot.set_level(camera_level)
        if camera_level == "error":
            self.preview.setPixmap(QPixmap())
            self.preview.setText(camera_text)
        self.calibration_status.setText(calibration[0])
        _fit_height(self.calibration_status, CONTENT_WIDTH - 50)
        self._calibration_dot.set_level(calibration[1])

        tracking_text, tracking_level = by_label.get("Tracking", ("", "idle"))
        worst = camera_level if camera_level in ("error", "warn") else tracking_level
        self._side_dot.set_level(worst)
        self._side_state.setText("Control on" if armed else "Control off")
        self._side_meta.setText(camera_text if camera_level == "error" else tracking_text)

    def status_text(self) -> str:
        """Everything the Home page currently says, for tests and bug reports."""
        lines = [self._hero_title.text(), self._hero_detail.text()]
        lines += [f"{label}: {text.text()}" for label, (_dot, text) in self._status_rows.items()]
        return "\n".join(lines)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.c.set_preview(self._nav.currentRow() == 1)  # reopened from the tray on Camera

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.c.quitting:
            event.accept()
            return
        if not self.c.tray_available:
            # Nothing could bring the window back: closing it ends SUPERHERO and the camera.
            event.accept()
            self.c.quit()
            return
        # Closing the panel keeps SUPERHERO running in the tray.
        self.c.set_preview(False)
        self.hide()
        event.ignore()
