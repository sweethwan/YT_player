"""Timeline mouse interaction and a nonactivating thumbnail tooltip."""

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)


def clock(seconds: float) -> str:
    value = max(0, int(seconds))
    hours, rest = divmod(value, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


class VideoWidget(QWidget):
    clicked = Signal()
    double_clicked = Signal()

    def mousePressEvent(self, event):
        self._double_release = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not getattr(self, "_double_release", False):
                self.clicked.emit()
            self._double_release = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._double_release = True
            self.double_clicked.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class Timeline(QSlider):
    seek_requested = Signal(float)
    hovered = Signal(float, QPoint)
    hover_left = Signal()

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setMouseTracking(True)
        self.setRange(0, 0)

    def value_at(self, x: float) -> int:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = style.subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
        )
        start = groove.x() + handle.width() / 2
        span = max(1, groove.width() - handle.width())
        offset = max(0, min(span, x - start))
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(offset), span, option.upsideDown
        )

    def _hover(self, event):
        if self.maximum() > self.minimum():
            self.hovered.emit(
                self.value_at(event.position().x()) / 1000,
                self.mapToGlobal(QPoint(int(event.position().x()), 0)),
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            self.setSliderDown(True)
            self.setValue(self.value_at(event.position().x()))
            self._hover(event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self.setValue(self.value_at(event.position().x()))
        self._hover(event)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setValue(self.value_at(event.position().x()))
            self.setSliderDown(False)
            self.seek_requested.emit(self.value() / 1000)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self.hover_left.emit()
        super().leaveEvent(event)


class PreviewPopup(QFrame):
    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            "QFrame {background:#16212e; border:1px solid #34465b;}"
            "QLabel {border:none; color:#e4edf7;}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        self.picture = QLabel("미리보기 준비 중")
        self.picture.setFixedSize(320, 180)
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setTextFormat(Qt.TextFormat.PlainText)
        self.time = QLabel()
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.picture)
        layout.addWidget(self.time)

    def present(self, seconds: float, anchor: QPoint):
        self.time.setText(clock(seconds))
        self.adjustSize()
        screen = self.screen()
        if self.parentWidget():
            screen = self.parentWidget().screen()
        bounds = screen.availableGeometry()
        x = max(
            bounds.left(), min(anchor.x() - self.width() // 2, bounds.right() - self.width() + 1)
        )
        y = anchor.y() - self.height() - 8
        if y < bounds.top():
            y = min(bounds.bottom() - self.height() + 1, anchor.y() + 20)
        self.move(x, y)
        self.show()

    def loading(self):
        self.picture.clear()
        self.picture.setText("미리보기 준비 중")

    def display(self, image: QImage | None, message: str = ""):
        self.picture.clear()
        if image is not None and not image.isNull():
            self.picture.setPixmap(
                QPixmap.fromImage(image).scaled(
                    320,
                    180,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.picture.setText(message or "미리보기 불가")
