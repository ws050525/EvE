"""主窗口：空态莫比乌斯 / 对话 / 输入。"""

from __future__ import annotations

import html

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .bridge import AgentBridge
from .mobius import MobiusWidget

GREETING = "今天想做点什么？"
SUBTITLE = "说句话，或者让我帮你干点活"

TOP_BAR = 44


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
        self._status_timer = None

        self._build()
        self._bridge = AgentBridge(config_path, self)
        self._wire()
        self._bridge.ready.connect(lambda *_: self._bridge.preload())

    # ---------- 构建界面 ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_top_bar())

        body = QVBoxLayout()
        body.setContentsMargins(48, 4, 48, 0)
        body.setSpacing(0)

        self.mobius = MobiusWidget(self, 220)
        body.addWidget(self.mobius, 0, Qt.AlignHCenter)

        # 空态整块：莫比乌斯 + 问候。对话开始后整块让位给消息区
        self.hero = QWidget(self)
        hero = QVBoxLayout(self.hero)
        hero.setContentsMargins(0, 0, 0, 0)
        hero.setSpacing(0)
        hero.addStretch(1)
        hero.addWidget(self.mobius, 0, Qt.AlignHCenter)
        hero.addSpacing(24)

        self.greeting = QLabel(GREETING, self.hero)
        self.greeting.setAlignment(Qt.AlignCenter)
        self.greeting.setStyleSheet(
            f"color: {theme.TEXT0}; font-family: {theme.FONT_STACK}; font-size: 20px;"
        )
        hero.addWidget(self.greeting)

        self.subtitle = QLabel(SUBTITLE, self.hero)
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setStyleSheet(
            f"color: {theme.TEXT2}; font-family: {theme.FONT_STACK}; font-size: 13px;"
        )
        hero.addWidget(self.subtitle)
        hero.addStretch(1)

        body.addWidget(self.hero, 1)

        self.browser = QTextBrowser(self)
        self.browser.setStyleSheet(theme.BROWSER_QSS)
        self.browser.setOpenExternalLinks(True)
        self.browser.hide()
        body.addWidget(self.browser, 0)

        self.status = QLabel("", self)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(
            f"color: {theme.TEXT2}; font-family: {theme.FONT_STACK}; font-size: 12px;"
        )
        self.status.setFixedHeight(20)
        body.addWidget(self.status)

        self._body = body
        root.addLayout(body, 1)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(48, 10, 48, 28)
        input_row.setSpacing(10)

        self.input = QLineEdit(self)
        self.input.setPlaceholderText("输入…")
        self.input.setStyleSheet(theme.INPUT_QSS)
        self.input.setMinimumHeight(44)
        self.input.returnPressed.connect(self._send)

        self.send_btn = QPushButton("发送", self)
        self.send_btn.setStyleSheet(theme.SEND_BUTTON_QSS)
        self.send_btn.setMinimumHeight(44)
        self.send_btn.clicked.connect(self._send)

        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_btn)
        root.addLayout(input_row)

        self._mobii = [self.mobius, self.mobius_small]

    def _build_top_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setFixedHeight(TOP_BAR)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 12, 0)
        layout.setSpacing(8)

        # 对话进行中，莫比乌斯缩到这儿
        self.mobius_small = MobiusWidget(bar, 28)
        self.mobius_small.hide()
        layout.addWidget(self.mobius_small)

        title = QLabel("EVE", bar)
        title.setStyleSheet(
            f"color: {theme.TEXT0}; font-family: {theme.FONT_STACK};"
            " font-size: 14px; font-weight: 600;"
        )
        layout.addWidget(title)
        layout.addStretch(1)

        self.mode_label = QLabel("chat", bar)
        self.mode_label.setStyleSheet(
            f"color: {theme.TEXT2}; font-family: {theme.FONT_STACK}; font-size: 12px;"
        )
        layout.addWidget(self.mode_label)
        layout.addSpacing(6)

        for text, slot in (("—", self.showMinimized), ("×", self.close)):
            btn = QPushButton(text, bar)
            btn.setFixedSize(32, 28)
            btn.setStyleSheet(theme.FLAT_BUTTON_QSS)
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        return bar

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
        self._append("你", text, theme.TEXT1)
        self._set_busy(True)
        self._set_status("思考中…")
        self._set_state("thinking")
        self._bridge.ask(text)

    def _set_state(self, state: str) -> None:
        for widget in self._mobii:
            widget.set_state(state)

    def _enter_chat(self) -> None:
        if self._started_chat:
            return
        self._started_chat = True
        self.hero.hide()
        self.mobius.hide()
        self.mobius_small.show()
        self.browser.show()
        self._body.setStretchFactor(self.hero, 0)
        self._body.setStretchFactor(self.browser, 1)
        # 半透明窗口不会自动清除被隐藏控件的区域，必须手动重绘
        self.update()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.input.setEnabled(not busy)
        self.send_btn.setEnabled(not busy)
        if not busy:
            self.input.setFocus()

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _append(self, who: str, text: str, color: str) -> None:
        body = html.escape(text).replace("\n", "<br/>")
        block = (
            '<div style="margin: 16px 0;">'
            f'<div style="color: {theme.TEXT2}; font-size: 11px; margin-bottom: 4px;">{who}</div>'
            f'<div style="color: {color}; font-size: 14px; line-height: 170%;">{body}</div>'
            "</div>"
        )
        self.browser.append(block)
        bar = self.browser.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------- 事件 ----------

    def _on_ready(self, model_id: str, label: str) -> None:
        self.mode_label.setText(label or model_id)

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
        self._set_state("idle")
        self._append("伊芙", text, theme.TEXT0)
        self._set_status("")
        self._set_busy(False)

    def _on_failed(self, message: str) -> None:
        self._set_state("error")
        self._append("系统", message, theme.DANGER)
        self._set_status("")
        self._set_busy(False)

    def demo_send(self, text: str) -> None:
        """开发期用：自动发一条消息，方便截图和冒烟测试。"""
        self.input.setText(text)
        self._send()

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
