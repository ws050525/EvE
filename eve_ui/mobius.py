"""莫比乌斯缎带：参数化绘制 + 状态动画。

不是 3D 网格，是 2.5D 的带子：圆环上取 N 段，沿环自转 180°，按深度排序后逐段填充。
好处是任意尺寸都读得出，且每帧只有几百个图元，CPU 足够。
"""

from __future__ import annotations

import math

from PySide6.QtCore import Property, QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QWidget

from . import theme

# 每种状态的转速（rad/s）与亮度系数
STATES = {
    "boot": (0.60, 0.70),
    "idle": (0.12, 0.78),
    "listening": (0.22, 0.88),
    "thinking": (0.90, 1.00),
    "streaming": (0.35, 0.95),
    "acting": (0.45, 0.98),
    "error": (0.00, 0.85),
    "muted": (0.00, 0.30),
}

SEGMENTS = 150
TILT_DEG = 26.0
FPS = 30


class MobiusWidget(QWidget):
    def __init__(self, parent=None, size: int = 220):
        super().__init__(parent)
        self._state = "idle"
        self._phase = 0.0
        self._clock = 0.0
        self._size = size
        self._min_size = 64
        self.setFixedSize(size, size)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(1000 / FPS))

    # ---------- 尺寸（可被动画驱动）----------

    def get_orb_size(self) -> int:
        return self._size

    def set_orb_size(self, value: int) -> None:
        value = max(self._min_size, int(value))
        if value == self._size:
            return
        self._size = value
        self.setFixedSize(value, value)
        self.update()

    orbSize = Property(int, get_orb_size, set_orb_size)

    # ---------- 状态 ----------

    def set_state(self, state: str) -> None:
        if state not in STATES:
            state = "idle"
        if state == self._state:
            return
        self._state = state
        self.update()

    @property
    def state(self) -> str:
        return self._state

    def _tick(self) -> None:
        speed, _ = STATES.get(self._state, STATES["idle"])
        self._clock += 1.0 / FPS
        self._phase += speed / FPS
        if speed > 0 or self._state not in ("muted",):
            self.update()

    # ---------- 绘制 ----------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        # 用实际尺寸而不是 _size：过渡动画期间控件会被拉成不同大小
        size = max(12, min(self.width(), self.height()))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        speed, brightness = STATES.get(self._state, STATES["idle"])
        breathe = 1.0 + 0.015 * math.sin(self._clock * 1.6)
        radius = size * 0.42 * breathe
        band = size * 0.062
        cx = cy = size / 2.0

        # 背景光晕（省钱版发光）；小尺寸不需要，否则糊成一团
        if size >= 90:
            # 半径必须收在控件内，否则渐变被裁切，会出现一块方形亮斑
            glow_radius = min(radius * 1.7, cx - 2, cy - 2)
            glow = QRadialGradient(cx, cy, glow_radius)
            alpha = int(46 * brightness)
            glow.setColorAt(0.0, QColor(theme.ACCENT_RGB[0], theme.ACCENT_RGB[1], theme.ACCENT_RGB[2], alpha))
            glow.setColorAt(0.5, QColor(theme.ACCENT_RGB[0], theme.ACCENT_RGB[1], theme.ACCENT_RGB[2], alpha // 3))
            glow.setColorAt(1.0, QColor(theme.ACCENT_RGB[0], theme.ACCENT_RGB[1], theme.ACCENT_RGB[2], 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(
                int(cx - glow_radius), int(cy - glow_radius),
                int(glow_radius * 2), int(glow_radius * 2),
            )

        if size < 90:
            # 小尺寸只保留轮廓：两段缺口相对的弧，读起来像一个扭转的环
            painter.setBrush(Qt.NoBrush)
            main_color = QColor(*theme.ACCENT_RGB)
            main_color.setAlphaF(min(1.0, 0.55 + 0.45 * brightness))
            pen = QPen(main_color, max(1.8, size * 0.055))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            box = (cx - radius * 0.92, cy - radius * 0.92, radius * 1.84, radius * 1.84)
            painter.drawArc(*[int(v) for v in (box[0], box[1], box[2], box[3])], 35 * 16, 250 * 16)

            dim = QColor(*theme.ACCENT_RGB)
            dim.setAlphaF(min(1.0, 0.35 + 0.35 * brightness))
            pen2 = QPen(dim, max(1.2, size * 0.03))
            pen2.setCapStyle(Qt.RoundCap)
            painter.setPen(pen2)
            inner = (cx - radius * 0.55, cy - radius * 0.55, radius * 1.10, radius * 1.10)
            painter.drawArc(*[int(v) for v in (inner[0], inner[1], inner[2], inner[3])], 215 * 16, 110 * 16)
            return

        tilt = math.radians(TILT_DEG)
        cos_t, sin_t = math.cos(tilt), math.sin(tilt)
        half = band

        def project(u: float, v: float) -> tuple[float, float, float]:
            r = radius + v * math.cos(u / 2.0)
            x = r * math.cos(u)
            y = r * math.sin(u)
            z = v * math.sin(u / 2.0)
            y2 = y * cos_t - z * sin_t
            z2 = y * sin_t + z * cos_t
            return cx + x, cy + y2, z2

        quads = []
        for i in range(SEGMENTS):
            u0 = 2 * math.pi * i / SEGMENTS
            u1 = 2 * math.pi * (i + 1) / SEGMENTS
            p0 = project(u0, -half)
            p1 = project(u0, half)
            p2 = project(u1, half)
            p3 = project(u1, -half)
            depth = (p0[2] + p1[2] + p2[2] + p3[2]) / 4.0
            quads.append((depth, (u0 + u1) / 2.0, (p0, p1, p2, p3)))

        quads.sort(key=lambda q: q[0])
        depths = [q[0] for q in quads]
        lo, hi = min(depths), max(depths)
        span = max(1e-6, hi - lo)
        two_pi = 2 * math.pi

        painter.setPen(Qt.NoPen)
        for depth, u, pts in quads:
            d = (depth - lo) / span
            delta = ((u - self._phase + math.pi) % two_pi) - math.pi
            highlight = math.exp(-(delta * delta) / 0.22)
            shade = min(1.0, (0.28 + 0.40 * d + 0.80 * highlight) * brightness)

            r = int(theme.ACCENT_RGB[0] * (1 - 0.30 * d) + theme.VIOLET_RGB[0] * 0.30 * d)
            g = int(theme.ACCENT_RGB[1] * (1 - 0.10 * d) + theme.VIOLET_RGB[1] * 0.10 * d)
            b = int(theme.ACCENT_RGB[2] * (1 - 0.05 * d) + theme.VIOLET_RGB[2] * 0.20 * d)

            color = QColor(
                min(255, int(r * shade)),
                min(255, int(g * shade)),
                min(255, int(b * shade)),
                int(96 + 159 * d),
            )
            poly = QPolygonF([QPointF(p[0], p[1]) for p in pts])
            painter.setBrush(color)
            painter.drawPolygon(poly)

    # ---------- 省电 ----------

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        self._timer.start(int(1000 / FPS))
        super().showEvent(event)
