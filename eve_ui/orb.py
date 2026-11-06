"""悬浮球：agent 模式的"在场形态"。

一个 84px 的玻璃球，两只光点眼。不抢焦点、可拖动、点一下回到主窗口。
表情是参数化的（眼形 / 视线 / 开合 / 色调），不做逐帧图片。
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPoint, QRect, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QLabel, QMenu, QVBoxLayout, QWidget

from . import theme

SIZE = 96
BALL = 40  # 半径
TICK_MS = 33


class BubbleWindow(QWidget):
    """球上方的小气泡，只显示"正在做什么"。"""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.title = QLabel("", self)
        self.title.setStyleSheet(
            f"color: {theme.TEXT0}; font-family: {theme.FONT_STACK}; font-size: 12px;"
        )
        self.detail = QLabel("", self)
        self.detail.setStyleSheet(
            f"color: {theme.TEXT1}; font-family: {theme.FONT_STACK}; font-size: 10px;"
        )
        self.detail.setWordWrap(True)
        self.detail.setMaximumWidth(210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        self.setFixedWidth(240)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(theme.BUBBLE))
        painter.setPen(QPen(QColor(theme.LINE), 1))
        painter.drawRoundedRect(rect, 10, 10)

    def show_message(self, title: str, detail: str = "", anchor: QWidget | None = None) -> None:
        self.title.setText(title)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.setFixedWidth(240)
        self.adjustSize()
        if anchor is not None:
            geo = anchor.frameGeometry()
            self.move(geo.right() - self.width(), max(0, geo.top() - self.height() - 10))
        self.show()
        self.raise_()
        self._hide_timer.start(6000)


class OrbWindow(QWidget):
    clicked = Signal()
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(SIZE, SIZE)

        self._state = "idle"
        self._clock = 0.0
        self._blink_countdown = 3.0
        self._eye_open = 1.0
        self._dragging = False
        self._drag_offset = QPoint()
        self._press_pos = QPoint()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)

        self._bubble = BubbleWindow()

    # ---------- 状态 ----------

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def bubble(self, title: str, detail: str = "") -> None:
        self._bubble.show_message(title, detail, self)

    # ---------- 动画 ----------

    def _tick(self) -> None:
        self._clock += TICK_MS / 1000.0

        # 随机眨眼：每 3-8 秒一次，持续 0.14 秒
        self._blink_countdown -= TICK_MS / 1000.0
        if self._blink_countdown <= 0:
            self._eye_open = 0.12
            self._blink_countdown = random.uniform(3.0, 8.0)
        elif self._eye_open < 1.0:
            self._eye_open = min(1.0, self._eye_open + 0.16)

        self.update()

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        cx = cy = SIZE / 2
        r = BALL

        accent = QColor(theme.DANGER) if self._state == "error" else QColor(theme.ACCENT)
        dim = 0.35 if self._state == "sleepy" else 1.0

        # 外发光
        glow = QRadialGradient(cx, cy, SIZE / 2 - 2)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), int(38 * dim)))
        glow.setColorAt(0.6, QColor(accent.red(), accent.green(), accent.blue(), int(14 * dim)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(2, 2, SIZE - 4, SIZE - 4)

        # 球体
        body = QRadialGradient(cx - r * 0.35, cy - r * 0.45, r * 1.9)
        body.setColorAt(0.0, QColor("#161C24"))
        body.setColorAt(1.0, QColor("#090C10"))
        painter.setBrush(body)
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # 内部极光（裁剪在球内）
        clip = QPainterPath()
        clip.addEllipse(cx - r, cy - r, r * 2, r * 2)
        painter.setClipPath(clip)
        self._aurora(painter, cx, cy, r, accent, dim)
        painter.setClipping(False)

        # 边缘：一圈暗描边 + 左上亮弧
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(46, 53, 64), 1.2))
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        bright = QColor(accent)
        bright.setAlphaF(min(1.0, 0.55 * dim + 0.35))
        pen = QPen(bright, 1.8)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(
            int(cx - r + 1), int(cy - r + 1), int(r * 2 - 2), int(r * 2 - 2),
            145 * 16, 175 * 16,
        )

        self._eyes(painter, cx, cy, r, accent, dim)

    def _aurora(self, painter: QPainter, cx: float, cy: float, r: float,
                accent: QColor, dim: float) -> None:
        drift = math.sin(self._clock * 0.5) * r * 0.08
        for dx, dy, radius, color, alpha in (
            (-r * 0.35 + drift, -r * 0.40, r * 0.85, QColor(60, 190, 170), 95),
            (r * 0.25, r * 0.20, r * 0.75, QColor(85, 110, 215), 70),
        ):
            g = QRadialGradient(cx + dx, cy + dy, radius)
            g.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), int(alpha * dim)))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(g)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(
                int(cx + dx - radius), int(cy + dy - radius), int(radius * 2), int(radius * 2)
            )

    def _eyes(self, painter: QPainter, cx: float, cy: float, r: float,
              accent: QColor, dim: float) -> None:
        ex, ey = r * 0.34, -r * 0.06
        ew, eh = r * 0.135, r * 0.185 * self._eye_open
        gaze = QPoint(0, 0)
        if self._state == "thinking":
            gaze = QPoint(int(r * 0.06), int(-r * 0.08))

        ink = QColor(accent)
        ink.setAlphaF(min(1.0, 0.55 + 0.45 * dim))
        painter.setPen(Qt.NoPen)
        painter.setBrush(ink)

        if self._state == "happy":
            pen = QPen(ink, max(1.6, r * 0.05))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            for sgn in (-1, 1):
                x = cx + sgn * ex
                painter.drawArc(
                    int(x - ew * 1.5), int(cy + ey - eh * 0.5),
                    int(ew * 3), int(eh * 1.6), 200 * 16, 140 * 16,
                )
            return

        if self._state in ("sleepy", "error"):
            pen = QPen(ink, max(1.4, r * 0.045))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            for sgn in (-1, 1):
                x = cx + sgn * ex
                painter.drawLine(
                    int(x - ew * 1.2), int(cy + ey), int(x + ew * 1.2), int(cy + ey)
                )
            return

        scale = 0.82 if self._state == "thinking" else 1.0
        for sgn in (-1, 1):
            x = cx + sgn * ex + gaze.x()
            y = cy + ey + gaze.y()
            painter.drawEllipse(
                QRect(int(x - ew * scale), int(y - eh * scale),
                      int(ew * 2 * scale), int(eh * 2 * scale))
            )

    # ---------- 交互 ----------

    def show_at_edge(self) -> None:
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.right() - SIZE - 24, area.center().y() - SIZE // 2)
        self.show()
        self.raise_()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_pos
            if abs(delta.x()) + abs(delta.y()) > 4:
                self._dragging = True
            if self._dragging:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                self._bubble.hide()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and not self._dragging:
            self.clicked.emit()
        self._dragging = False
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.setStyleSheet(theme.MENU_QSS)
        menu.addAction("打开主窗口", self.clicked.emit)
        menu.addSeparator()
        menu.addAction("退出", self.quit_requested.emit)
        menu.exec(event.globalPos())

    def closeEvent(self, event) -> None:  # noqa: N802
        self._bubble.close()
        super().closeEvent(event)
