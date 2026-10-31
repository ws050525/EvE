"""主窗口：空态莫比乌斯 / 聊天气泡 / 输入。

结构上分三块：
- 顶栏：状态点、档位、模型、窗口按钮
- 主体：空态（hero）或消息列表（ChatView），二选一
- 底部：状态行、分隔线、输入区
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .bridge import AgentBridge
from .chat_view import ChatView
from .mobius import MobiusWidget

GREETING = "今天想做点什么？"
SUBTITLE = "说句话，或者让我帮你干点活"
SUGGESTIONS = ("打开记事本", "解释一下什么是 Agent", "帮我理一下今天的待办")

TOP_BAR = 44
SIDE = 96


class ChatWindow(QWidget):
    def __init__(self, config_path: str | None = None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(960, 640)
        self.setMinimumSize(760, 540)

        self._dragging: object | None = None
        self._busy = False
        self._started_chat = False
        self._streaming = False

        self._build()
        self._bridge = AgentBridge(config_path, self)
        self._wire()
        self._bridge.ready.connect(lambda *_: self._bridge.preload())

    # ---------- 构建 ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())

        body = QVBoxLayout()
        body.setContentsMargins(SIDE, 4, SIDE, 0)
        body.setSpacing(0)

        self.hero = self._build_hero()
        body.addWidget(self.hero, 1)

        self.chat = ChatView(self)
        self.chat.hide()
        body.addWidget(self.chat, 0)

        self.status = QLabel("", self)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(
            f"color: {theme.TEXT2}; font-family: {theme.FONT_STACK}; font-size: 12px;"
        )
        self.status.setFixedHeight(20)
        body.addWidget(self.status)

        self._body = body
        root.addLayout(body, 1)

        divider = QFrame(self)
        divider.setFixedHeight(1)
        divider.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            " stop:0 rgba(37,42,51,0), stop:0.5 #252A33, stop:1 rgba(37,42,51,0));"
        )
        root.addWidget(divider)
        root.addSpacing(10)

        root.addLayout(self._build_input_row())

    def _build_top_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setFixedHeight(TOP_BAR)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 12, 0)
        layout.setSpacing(8)

        self.mobius_small = MobiusWidget(bar, 26)
        self.mobius_small.hide()
        layout.addWidget(self.mobius_small)

        title = QLabel("EVE", bar)
        title.setStyleSheet(
            f"color: {theme.TEXT0}; font-family: {theme.FONT_STACK};"
            " font-size: 14px; font-weight: 600;"
        )
        layout.addWidget(title)
        layout.addStretch(1)

        self.dot = QLabel(bar)
        self.dot.setFixedSize(7, 7)
        self._set_dot(theme.TEXT2)
        layout.addWidget(self.dot)
        layout.addSpacing(4)

        self.mode_chip = QLabel("chat", bar)
        self.mode_chip.setFixedHeight(20)
        self.mode_chip.setStyleSheet(
            f"color: {theme.TEXT1}; background: {theme.PANEL};"
            f" border: 1px solid {theme.LINE}; border-radius: 10px;"
            f" padding: 0 9px; font-family: {theme.FONT_STACK}; font-size: 11px;"
        )
        layout.addWidget(self.mode_chip, 0, Qt.AlignVCenter)
        layout.addSpacing(4)

        self.model_label = QLabel("", bar)
        self.model_label.setStyleSheet(
            f"color: {theme.TEXT2}; font-family: {theme.FONT_STACK}; font-size: 11px;"
            " padding-right: 4px;"
        )
        layout.addWidget(self.model_label)

        for text, slot in (("—", self.showMinimized), ("×", self.close)):
            btn = QPushButton(text, bar)
            btn.setFixedSize(30, 26)
            btn.setStyleSheet(theme.FLAT_BUTTON_QSS)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        return bar

    def _build_hero(self) -> QWidget:
        hero = QWidget(self)
        layout = QVBoxLayout(hero)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch(1)

        self.mobius = MobiusWidget(hero, 220)
        layout.addWidget(self.mobius, 0, Qt.AlignHCenter)
        layout.addSpacing(26)

        greeting = QLabel(GREETING, hero)
        greeting.setAlignment(Qt.AlignCenter)
        greeting.setStyleSheet(
            f"color: {theme.TEXT0}; font-family: {theme.FONT_STACK}; font-size: 20px;"
        )
        layout.addWidget(greeting)

        subtitle = QLabel(SUBTITLE, hero)
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(
            f"color: {theme.TEXT2}; font-family: {theme.FONT_STACK}; font-size: 13px;"
        )
        layout.addWidget(subtitle)

        chips = QWidget(hero)
        row = QHBoxLayout(chips)
        row.setContentsMargins(0, 22, 0, 0)
        row.setSpacing(10)
        row.addStretch(1)
        for label in SUGGESTIONS:
            chip = QPushButton(label, chips)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(theme.CHIP_QSS)
            chip.clicked.connect(lambda _=False, text=label: self._use_chip(text))
            row.addWidget(chip)
        row.addStretch(1)
        layout.addWidget(chips)

        layout.addStretch(1)
        return hero

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(SIDE, 12, SIDE, 26)
        row.setSpacing(10)

        self.input = QLineEdit(self)
        self.input.setPlaceholderText("输入…")
        self.input.setStyleSheet(theme.INPUT_QSS)
        self.input.setMinimumHeight(44)
        self.input.returnPressed.connect(self._send)

        self.send_btn = QPushButton("发送", self)
        self.send_btn.setStyleSheet(theme.SEND_BUTTON_QSS)
        self.send_btn.setMinimumHeight(44)
        self.send_btn.clicked.connect(self._send)

        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        return row

    # ---------- 接线 ----------

    def _wire(self) -> None:
        self._bridge.ready.connect(self._on_ready)
        self._bridge.event.connect(self._on_event)
        self._bridge.replied.connect(self._on_replied)
        self._bridge.failed.connect(self._on_failed)

    # ---------- 交互 ----------

    def _send(self) -> None:
        if self._busy:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._enter_chat()
        self.chat.add_message("user", text)
        self._set_busy(True)
        self._set_status("思考中…")
        self._set_state("thinking")
        self._bridge.ask(text)

    def _use_chip(self, text: str) -> None:
        if self._busy:
            return
        self.input.setText(text)
        self._send()

    def _enter_chat(self) -> None:
        """空态 → 对话态：莫比乌斯从中间飞到顶栏，其余内容交叉淡入淡出。"""
        if self._started_chat:
            return
        self._started_chat = True

        # 1. 把大环从 hero 布局里摘出来，变成窗口上的浮层，才能自由飞
        start_rect = QRect(self.mobius.mapTo(self, QPoint(0, 0)), self.mobius.size())
        self.hero.layout().removeWidget(self.mobius)
        self.mobius.setParent(self)
        self.mobius.setMinimumSize(1, 1)
        self.mobius.setMaximumSize(2000, 2000)
        self.mobius.setGeometry(start_rect)
        self.mobius.show()
        self.mobius.raise_()

        # 2. 目标位置：顶栏里那个小环
        self.mobius_small.show()
        small_effect = QGraphicsOpacityEffect(self.mobius_small)
        small_effect.setOpacity(0.0)
        self.mobius_small.setGraphicsEffect(small_effect)
        target_rect = QRect(
            self.mobius_small.mapTo(self, QPoint(0, 0)), self.mobius_small.size()
        )

        # 3. hero 淡出
        hero_effect = QGraphicsOpacityEffect(self.hero)
        self.hero.setGraphicsEffect(hero_effect)
        hero_fade = QPropertyAnimation(hero_effect, b"opacity", self)
        hero_fade.setDuration(170)
        hero_fade.setStartValue(1.0)
        hero_fade.setEndValue(0.0)
        hero_fade.setEasingCurve(QEasingCurve.OutCubic)
        hero_fade.finished.connect(self._finish_hero_fade)
        hero_fade.start(QPropertyAnimation.DeleteWhenStopped)

        # 4. 对话区淡入（稍微延后，避免两件事同时抢注意力）
        self.chat.show()
        chat_effect = QGraphicsOpacityEffect(self.chat)
        self.chat.setGraphicsEffect(chat_effect)
        chat_fade = QPropertyAnimation(chat_effect, b"opacity", self)
        chat_fade.setDuration(280)
        chat_fade.setStartValue(0.0)
        chat_fade.setEndValue(1.0)
        chat_fade.setEasingCurve(QEasingCurve.OutCubic)
        chat_fade.finished.connect(lambda: self.chat.setGraphicsEffect(None))
        QTimer.singleShot(110, lambda: chat_fade.start(QPropertyAnimation.DeleteWhenStopped))

        # 5. 主动画：大环飞到顶栏并缩到 26px
        self._fly = QPropertyAnimation(self.mobius, b"geometry", self)
        self._fly.setDuration(420)
        self._fly.setStartValue(start_rect)
        self._fly.setEndValue(target_rect)
        self._fly.setEasingCurve(QEasingCurve.OutCubic)
        self._fly.finished.connect(lambda: self._finish_fly(small_effect))
        self._fly.start(QPropertyAnimation.DeleteWhenStopped)

        self._body.setStretchFactor(self.hero, 0)
        self._body.setStretchFactor(self.chat, 1)
        self.update()

    def _finish_hero_fade(self) -> None:
        self.hero.hide()
        self.hero.setGraphicsEffect(None)
        self.update()

    def _finish_fly(self, small_effect: QGraphicsOpacityEffect) -> None:
        self.mobius.hide()
        small_effect.setOpacity(1.0)
        self.mobius_small.setGraphicsEffect(None)
        self.update()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.input.setEnabled(not busy)
        self.send_btn.setEnabled(not busy)
        if not busy:
            self.input.setFocus()

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _set_dot(self, color: str) -> None:
        self.dot.setStyleSheet(f"background: {color}; border-radius: 3px;")

    def _set_state(self, state: str) -> None:
        for widget in (self.mobius, self.mobius_small):
            widget.set_state(state)
        if state in ("thinking", "streaming", "acting", "listening"):
            self._set_dot(theme.ACCENT)
        elif state == "error":
            self._set_dot(theme.DANGER)
        elif state == "boot":
            self._set_dot(theme.WARN)
        else:
            self._set_dot("#3A4350")

    def demo_send(self, text: str) -> None:
        """开发期用：自动发一条消息，方便截图和冒烟测试。"""
        self.input.setText(text)
        self._send()

    # ---------- 事件 ----------

    def _on_ready(self, model_id: str, label: str) -> None:
        self.model_label.setText(label or model_id)

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "engine_starting":
            self._set_state("boot")
            self._set_status("加载模型…")
        elif kind == "engine_ready":
            self._set_state("idle")
            seconds = data.get("load_seconds")
            self._set_status(f"就绪 {seconds}s" if seconds else "就绪")
        elif kind == "engine_stopped":
            self._set_state("muted")
            self._set_status("引擎已停止")
        elif kind == "thinking":
            self._set_state("thinking")
            self._set_status("思考中…")
        elif kind == "reply_chunk":
            if not self._streaming:
                self._streaming = True
                self._set_state("streaming")
                self._set_status("")
            self.chat.push_chunk(data.get("text", ""))
        elif kind == "tool_start":
            self._set_state("acting")
            self._set_status(f"执行 {data.get('name')}…")
        elif kind == "tool_end":
            ok = data.get("ok")
            self._set_status(f"{data.get('name')} {'完成' if ok else '失败'}")
        elif kind == "error":
            self._set_state("error")
            self._set_status("出错了")

    def _on_replied(self, text: str) -> None:
        if self._streaming:
            self.chat.end_stream(text)
            self._streaming = False
        else:
            self.chat.add_message("assistant", text)
        self._set_state("idle")
        self._set_status("")
        self._set_busy(False)

    def _on_failed(self, message: str) -> None:
        self._streaming = False
        self._set_state("error")
        self.chat.add_message("system", message)
        self._set_status("")
        self._set_busy(False)

    # ---------- 窗口 ----------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(theme.BG))
        painter.setPen(QPen(QColor(theme.LINE), 1))
        painter.drawRoundedRect(rect, 16, 16)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and event.position().y() < TOP_BAR:
            self._dragging = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._dragging)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._bridge.shutdown()
        finally:
            super().closeEvent(event)
