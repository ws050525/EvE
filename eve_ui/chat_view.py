"""聊天视图：头像 + 气泡 + 时间戳，支持流式追加。

为什么不用 QTextBrowser：左右对齐的气泡、头像、进入动画，用富文本文档做都要靠表格硬凑，
而且流式追加时要处理游标位置。拆成一行一个控件之后，这些都变成普通的布局问题。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import theme

AVATAR = 32
MAX_BUBBLE = 560
STAMP_GAP = 180  # 两次消息间隔超过这个秒数，就插一条时间戳


class Avatar(QWidget):
    """32px 圆形头像。伊芙是青绿弧，用户是中性灰弧。"""

    def __init__(self, role: str, size: int = AVATAR, parent=None):
        super().__init__(parent)
        self._role = role
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#151920"))
        painter.drawEllipse(rect)

        accent = {
            "assistant": QColor(theme.ACCENT),
            "system": QColor(theme.DANGER),
        }.get(self._role, QColor("#3A4350"))

        # 外圈 + 中心点：比一段弧更像头像，也不至于被误读成字母 C
        pen = QPen(accent, 1.6)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(pen)
        painter.drawEllipse(rect.adjusted(3, 3, -3, -3))

        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        center = rect.center()
        painter.drawEllipse(center, 3, 3)


class MessageRow(QWidget):
    def __init__(self, role: str, text: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self.avatar = Avatar(role, AVATAR, self)

        self.bubble = QLabel(text, self)
        self.bubble.setWordWrap(True)
        self.bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.bubble.setMaximumWidth(MAX_BUBBLE)
        self.bubble.setStyleSheet(self._bubble_qss(role))

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        if role == "user":
            row.addStretch(1)
            row.addWidget(self.bubble, 0, Qt.AlignTop)
            row.addWidget(self.avatar, 0, Qt.AlignTop)
        else:
            row.addWidget(self.avatar, 0, Qt.AlignTop)
            row.addWidget(self.bubble, 0, Qt.AlignTop)
            row.addStretch(1)

        if text:
            self.set_text(text)

    @staticmethod
    def _bubble_qss(role: str) -> str:
        if role == "user":
            background, color, bl, br = "#17302E", theme.TEXT0, 4, 12
        elif role == "system":
            background, color, bl, br = "#2A1D1D", theme.DANGER, 12, 12
        else:
            background, color, bl, br = theme.PANEL, theme.TEXT0, 12, 4
        return (
            f"background: {background};"
            f" border-top-left-radius: 12px; border-top-right-radius: 12px;"
            f" border-bottom-left-radius: {bl}px; border-bottom-right-radius: {br}px;"
            f" padding: 10px 14px; color: {color};"
            f" font-family: {theme.FONT_STACK}; font-size: 14px;"
        )

    def set_text(self, text: str) -> None:
        self.bubble.setText(text)
        # 让气泡按内容宽度伸展，但只增不减，避免流式时来回抖
        metrics = self.bubble.fontMetrics()
        longest = max(
            (metrics.horizontalAdvance(line) for line in text.split("\n")), default=0
        )
        target = min(MAX_BUBBLE, max(96, longest + 34))
        if target > self.bubble.width():
            self.bubble.setFixedWidth(target)
        self.bubble.updateGeometry()


class ChatView(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 滚动区默认会画一层 palette Base 底色，在半透明窗口里就是一块浅灰。
        # 用对象名精确指定，避免样式级联到气泡。
        self.setObjectName("chatView")
        self.setStyleSheet(
            theme.BROWSER_QSS + " QScrollArea#chatView { background: transparent; border: none; }"
        )
        self.viewport().setObjectName("chatViewport")
        self.viewport().setStyleSheet("#chatViewport { background: transparent; }")
        self.viewport().setAutoFillBackground(False)

        self._container = QWidget(self)
        self._container.setObjectName("chatContainer")
        self._container.setStyleSheet("#chatContainer { background: transparent; }")
        self._container.setAutoFillBackground(False)
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 6, 0, 6)
        self._layout.setSpacing(14)
        self._layout.addStretch(1)
        self.setWidget(self._container)

        self._last_time: datetime | None = None
        self._stream_row: MessageRow | None = None
        self._stream_buffer = ""

    # ---------- 布局辅助 ----------

    def _insert(self, widget: QWidget) -> None:
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def _stamp(self) -> None:
        now = datetime.now()
        if self._last_time and (now - self._last_time).total_seconds() < STAMP_GAP:
            return
        label = QLabel(now.strftime("%H:%M"), self._container)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(
            f"color: {theme.TEXT2}; font-family: {theme.FONT_STACK}; font-size: 11px;"
            " padding: 6px 0 2px 0;"
        )
        self._insert(label)
        self._last_time = now

    def _fade_in(self, widget: QWidget) -> None:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def _scroll_bottom(self) -> None:
        QTimer.singleShot(0, self._do_scroll)

    def _do_scroll(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------- 对外 ----------

    def add_message(self, role: str, text: str) -> None:
        self._stamp()
        row = MessageRow(role, text, self._container)
        self._insert(row)
        self._fade_in(row)
        self._scroll_bottom()

    def begin_stream(self) -> None:
        self._stamp()
        self._stream_buffer = ""
        self._stream_row = MessageRow("assistant", "", self._container)
        self._insert(self._stream_row)
        self._fade_in(self._stream_row)
        self._scroll_bottom()

    def push_chunk(self, delta: str) -> None:
        if self._stream_row is None:
            self.begin_stream()
        self._stream_buffer += delta
        self._stream_row.set_text(self._stream_buffer)
        self._scroll_bottom()

    def end_stream(self, final: str) -> None:
        if self._stream_row is not None:
            self._stream_row.set_text(final)
            self._stream_row = None
            self._stream_buffer = ""
            self._scroll_bottom()

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._last_time = None
        self._stream_row = None
        self._stream_buffer = ""
