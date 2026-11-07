"""主窗口：空态莫比乌斯 / 聊天气泡 / 输入。

结构上分三块：
- 顶栏：状态点、档位、模型、窗口按钮
- 主体：空态（hero）或消息列表（ChatView），二选一
- 底部：状态行、分隔线、输入区
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .bridge import AgentBridge
from .chat_view import ChatView
from .mobius import MobiusWidget
from .orb import OrbWindow
from .tts import TtsBridge
from .voice import VoiceBridge, WakeBridge
from eve_core.config import load_config, save_voice_preference
from eve_core.pricing import format_money
from eve_core.tts import TtsSettings, list_voice_models
from eve_core.voice import VoiceSettings, match_wake, mode_aliases, parse_mode_command

GREETING = "今天想做点什么？"
SUBTITLE = "说句话，或者让我帮你干点活"
SUGGESTIONS = ("打开记事本", "解释一下什么是 Agent", "帮我理一下今天的待办")

# 唤醒后的应答语。要短：两个字合成快，不会让人干等
WAKE_REPLY = "Boss，我在"

TOP_BAR = 44
SIDE = 96


class VoiceDialog(QDialog):
    """选音色：列表里挑一个，点「试听」会真的念一句，满意再点「使用」。"""

    def __init__(
        self,
        voices: list[dict],
        current_path: str,
        preview,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("选择音色")
        self.setStyleSheet(theme.DIALOG_QSS)
        self.setMinimumWidth(440)
        self._voices = list(voices)
        self._preview = preview

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel("伊芙的声音", self)
        title.setStyleSheet(
            f"color: {theme.TEXT0}; font-family: {theme.FONT_STACK}; font-size: 13px;"
        )
        layout.addWidget(title)

        self.list = QListWidget(self)
        self.list.setStyleSheet(
            f"QListWidget {{ background: {theme.BUBBLE}; border: 1px solid {theme.LINE};"
            f" border-radius: 8px; color: {theme.TEXT0};"
            f" font-family: {theme.FONT_STACK}; font-size: 12px; padding: 4px; }}"
            f" QListWidget::item {{ padding: 8px; border-radius: 6px; }}"
            f" QListWidget::item:selected {{ background: {theme.PANEL}; color: {theme.TEXT0}; }}"
        )
        current_row = 0
        for index, voice in enumerate(self._voices):
            item = QListWidgetItem(f"{voice['label']}    {voice['id']}")
            item.setData(Qt.UserRole, voice["path"])
            self.list.addItem(item)
            if voice["path"] == current_path:
                current_row = index
        self.list.setCurrentRow(current_row)
        layout.addWidget(self.list, 1)

        hint = QLabel(
            "换过的音色会记在 .data/voice.json，重启后仍然是这个。", self
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        preview_btn = QPushButton("试听", self)
        preview_btn.setCursor(Qt.PointingHandCursor)
        preview_btn.setStyleSheet(theme.DENY_BUTTON_QSS)
        preview_btn.clicked.connect(self._on_preview)
        buttons.addWidget(preview_btn)
        buttons.addStretch(1)

        cancel = QPushButton("取消", self)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(theme.DENY_BUTTON_QSS)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        use = QPushButton("使用", self)
        use.setCursor(Qt.PointingHandCursor)
        use.setStyleSheet(theme.ALLOW_BUTTON_QSS)
        use.clicked.connect(self.accept)
        buttons.addWidget(use)
        layout.addLayout(buttons)

    def chosen(self) -> dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        path = str(item.data(Qt.UserRole))
        for voice in self._voices:
            if voice["path"] == path:
                return voice
        return None

    def _on_preview(self) -> None:
        voice = self.chosen()
        if voice is not None and self._preview is not None:
            self._preview(voice["path"])


class ConfirmCard(QFrame):
    """主窗口里的许可卡片。

    替掉原来的 QMessageBox：系统弹窗跟这套深色界面完全不搭，
    而且模态框会抢焦点、挡住正在看的东西。这张卡片浮在输入框上方，
    不挡对话、不抢焦点，两个按钮跟悬浮球气泡里的是同一套样式。
    """

    decided = Signal(bool)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("ConfirmCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(theme.CONFIRM_CARD_QSS)
        self.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self.title = QLabel("", self)
        self.title.setWordWrap(True)
        self.title.setStyleSheet(
            f"color: {theme.TEXT0}; font-family: {theme.FONT_STACK}; font-size: 12px;"
        )
        layout.addWidget(self.title)

        self.detail = QLabel("", self)
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(
            f"color: {theme.TEXT1}; font-family: {theme.FONT_STACK}; font-size: 11px;"
        )
        layout.addWidget(self.detail)

        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)
        self.deny_btn = QPushButton("拒绝", self)
        self.deny_btn.setCursor(Qt.PointingHandCursor)
        self.deny_btn.setStyleSheet(theme.DENY_BUTTON_QSS)
        self.deny_btn.clicked.connect(lambda: self._decide(False))
        row.addWidget(self.deny_btn)

        self.allow_btn = QPushButton("允许", self)
        self.allow_btn.setCursor(Qt.PointingHandCursor)
        self.allow_btn.setStyleSheet(theme.ALLOW_BUTTON_QSS)
        self.allow_btn.clicked.connect(lambda: self._decide(True))
        row.addWidget(self.allow_btn)
        layout.addLayout(row)

        self._pending = False
        self.setFixedWidth(420)

    def show_for(self, name: str, args: dict) -> None:
        self._pending = True
        self.title.setText(f"允许我执行 {name}？")
        preview = "，".join(f"{k}={v}" for k, v in args.items()) or "（无参数）"
        if len(preview) > 260:
            preview = preview[:260] + "…"
        self.detail.setText(preview)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        anchor = getattr(parent, "input", None)
        if anchor is not None:
            top = anchor.mapTo(parent, QPoint(0, 0)).y()
        else:
            top = parent.height() - 80
        y = max(TOP_BAR + 8, top - self.height() - 14)
        self.move((parent.width() - self.width()) // 2, y)

    def _decide(self, allowed: bool) -> None:
        if not self._pending:
            return
        self._pending = False
        self.hide()
        self.decided.emit(allowed)

    def cancel(self) -> None:
        if self._pending:
            self._pending = False
            self.hide()
            self.decided.emit(False)


class KeyDialog(QDialog):
    """给云端模型配 Key。

    Key 从来只走内存 → DPAPI 加密落盘，界面不回显、不写进 config.toml。
    """

    def __init__(self, rows: list[dict], parent: QWidget | None = None, preselect: str = ""):
        super().__init__(parent)
        self.setWindowTitle("设置 API Key")
        self.setStyleSheet(theme.DIALOG_QSS)
        self.setMinimumWidth(420)
        self.action = ""  # save | delete
        self._rows = list(rows)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel("给云端模型配一把 API Key", self)
        title.setStyleSheet(
            f"color: {theme.TEXT0}; font-family: {theme.FONT_STACK}; font-size: 13px;"
        )
        layout.addWidget(title)

        self.combo = QComboBox(self)
        for row in self._rows:
            mark = "　✓ 已配置" if row.get("has_key") else "　（未配置）"
            self.combo.addItem(f"{row['label']}{mark}", row["id"])
        if preselect:
            index = self.combo.findData(preselect)
            if index >= 0:
                self.combo.setCurrentIndex(index)
        layout.addWidget(self.combo)

        self.edit = QLineEdit(self)
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText("粘贴 API Key（不会回显）")
        self.edit.setStyleSheet(theme.INPUT_QSS)
        self.edit.returnPressed.connect(self._on_save)
        layout.addWidget(self.edit)

        hint = QLabel(
            "用 Windows DPAPI 加密后存到 .data/keys.json，只有当前 Windows 用户能解开。"
            "改完立刻生效，不用重启。",
            self,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.error = QLabel("", self)
        self.error.setStyleSheet(
            f"color: {theme.DANGER}; font-family: {theme.FONT_STACK}; font-size: 11px;"
        )
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.delete_btn = QPushButton("清除已保存的 Key", self)
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setStyleSheet(theme.DENY_BUTTON_QSS)
        self.delete_btn.clicked.connect(self._on_delete)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)

        cancel = QPushButton("取消", self)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(theme.DENY_BUTTON_QSS)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        save = QPushButton("保存", self)
        save.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet(theme.ALLOW_BUTTON_QSS)
        save.clicked.connect(self._on_save)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        self.edit.setFocus()

    def key_id(self) -> str:
        return str(self.combo.currentData() or "")

    def secret(self) -> str:
        return self.edit.text().strip()

    def _on_save(self) -> None:
        if not self.secret():
            self.error.setText("Key 不能为空")
            return
        self.action = "save"
        self.accept()

    def _on_delete(self) -> None:
        row = next((r for r in self._rows if r["id"] == self.key_id()), None)
        if row is not None and not row.get("has_key"):
            self.error.setText("这个 id 本来就没有保存过 Key")
            return
        self.action = "delete"
        self.accept()


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
        self._modes: list[str] = []
        self._models: list[dict] = []
        self._key_rows: list[dict] = []
        self._mode = "chat"
        self._switching = False
        self._tool_row = None
        self._last_usage: dict = {}
        self._last_latency = 0
        self._session_cost = 0.0
        self._ctx_setting = 0  # 0 = 自动
        self._ctx_effective = 0
        self._steps = 12
        self._pending_confirm: dict | None = None

        self._build()
        self._bridge = AgentBridge(config_path, self, autostart=False)
        self.confirm_card = ConfirmCard(self)
        self.confirm_card.decided.connect(self._resolve_pending)
        self._orb = OrbWindow()
        self._orb.clicked.connect(self._restore_from_orb)
        self._orb.quit_requested.connect(self._quit_app)
        self._orb.confirm_answered.connect(self._resolve_pending)

        self._voice_settings, self._data_dir = self._load_voice_settings(config_path)
        self._voice = VoiceBridge(self._voice_settings, self)
        self._voice.state_changed.connect(self._on_voice_state)
        self._voice.transcript.connect(self._on_voice_text)
        self._voice.failed.connect(self._on_voice_failed)
        self._voice_listening = False
        self._collapsing = False  # 正在收成悬浮球（防重入）
        self._pending_listen = False  # 唤醒应答播完后接着开麦
        self._mode_aliases: dict[str, str] = {}

        self._wake = None
        if self._voice_settings.kws_enabled:
            self._wake = WakeBridge(self._voice_settings, self)
            self._wake.woke.connect(self._on_wake)
            self._wake.failed.connect(lambda msg: self._set_status(msg))

        self._tts_enabled = bool(self._voice_settings.tts_enabled)
        self._tts = TtsBridge(
            TtsSettings(
                model=self._voice_settings.tts_model,
                speed=self._voice_settings.tts_speed,
                speaker=self._voice_settings.tts_speaker,
                enabled=self._tts_enabled,
            ),
            self,
        )
        self._tts.started.connect(lambda: self._set_status("播报中…"))
        self._tts.finished.connect(self._on_tts_finished)
        self._tts.failed.connect(lambda msg: self._set_status(msg))
        self._wire()
        # 指定了 EVE_MODEL 时跳过本地预加载，避免白起一次本地引擎
        if not os.environ.get("EVE_MODEL"):
            self._bridge.ready.connect(lambda *_: self._bridge.preload())
        self._bridge.start()

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

        self.mode_chip = QPushButton("chat", bar)
        self.mode_chip.setFixedHeight(20)
        self.mode_chip.setCursor(Qt.PointingHandCursor)
        self.mode_chip.setStyleSheet(theme.MODE_CHIP_QSS)
        self.mode_chip.clicked.connect(self._open_mode_menu)
        layout.addWidget(self.mode_chip, 0, Qt.AlignVCenter)
        layout.addSpacing(4)

        self.ctx_chip = QPushButton("ctx", bar)
        self.ctx_chip.setFixedHeight(20)
        self.ctx_chip.setCursor(Qt.PointingHandCursor)
        self.ctx_chip.setStyleSheet(theme.MODE_CHIP_QSS)
        self.ctx_chip.setToolTip("上下文预算与步数上限")
        self.ctx_chip.clicked.connect(self._open_limits_menu)
        layout.addWidget(self.ctx_chip, 0, Qt.AlignVCenter)
        layout.addSpacing(4)

        self.model_label = QPushButton("", bar)
        self.model_label.setCursor(Qt.PointingHandCursor)
        self.model_label.setToolTip("点这里换模型")
        self.model_label.setFlat(True)
        self.model_label.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {theme.TEXT2};"
            f" font-family: {theme.FONT_STACK}; font-size: 11px; padding: 0 4px; }}"
            f" QPushButton:hover {{ color: {theme.TEXT0}; }}"
        )
        self.model_label.clicked.connect(self._open_model_menu)
        layout.addWidget(self.model_label, 0, Qt.AlignVCenter)

        self.cost_label = QLabel("", bar)
        self.cost_label.setToolTip("本次会话累计估算花费（本地模型不计费）")
        self.cost_label.setStyleSheet(
            f"color: {theme.TEXT2}; font-family: {theme.FONT_STACK}; font-size: 11px;"
            " padding-right: 6px;"
        )
        layout.addWidget(self.cost_label)

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
        self.input.installEventFilter(self)

        self.send_btn = QPushButton("发送", self)
        self.send_btn.setStyleSheet(theme.SEND_BUTTON_QSS)
        self.send_btn.setMinimumHeight(44)
        self.send_btn.clicked.connect(self._send)

        self.voice_btn = QPushButton("语音", self)
        self.voice_btn.setCursor(Qt.PointingHandCursor)
        self.voice_btn.setStyleSheet(theme.VOICE_BUTTON_QSS)
        self.voice_btn.setMinimumHeight(44)
        self.voice_btn.setFixedWidth(72)
        self.voice_btn.setToolTip("点一下开始说，说完停一下自动结束（空格 / Ctrl+Space）")
        self.voice_btn.clicked.connect(self._on_voice)

        row.addWidget(self.input, 1)
        row.addWidget(self.voice_btn)
        row.addWidget(self.send_btn)
        return row

    # ---------- 接线 ----------

    def _wire(self) -> None:
        self._bridge.ready.connect(self._on_ready)
        self._bridge.event.connect(self._on_event)
        self._bridge.replied.connect(self._on_replied)
        self._bridge.failed.connect(self._on_failed)
        self._bridge.mode_changed.connect(self._on_mode_changed)
        self._bridge.modes_ready.connect(self._on_modes_ready)
        self._bridge.models_ready.connect(self._on_models_ready)
        self._bridge.keys_ready.connect(self._on_keys_ready)
        self._bridge.limits_changed.connect(self._on_limits_changed)
        self._bridge.confirm_required.connect(self._on_confirm_required)

        QShortcut(QKeySequence("Ctrl+Space"), self, activated=self._on_voice)

    # ---------- 交互 ----------

    def _send(self) -> None:
        if self._busy:
            return
        if self._switching:
            self._set_status("正在切换档位，稍等一下…")
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()

        # 「切换到工作」这类话直接执行，不用绕一圈让模型来决定
        target = parse_mode_command(text, self._mode_aliases)
        if target is not None:
            self._enter_chat()
            self.chat.add_message("user", text)
            if target == self._mode:
                self.chat.add_note(f"现在就是{self._mode_label(target)}档")
            else:
                self._switch_mode(target)
            return

        self._enter_chat()
        self.chat.add_message("user", text)
        self._set_busy(True)
        self._pause_wake()
        self._set_status("思考中…")
        self._set_state("thinking")
        self._bridge.ask(text)
        if self._mode == "desktop":
            QTimer.singleShot(150, self._collapse_to_orb)

    def _use_chip(self, text: str) -> None:
        if self._busy:
            return
        self.input.setText(text)
        self._send()

    def _send_text(self, text: str) -> None:
        """给语音用：把识别结果当作用户输入发出去。"""
        self.input.setText(text)
        self._send()

    # ---------- 语音 ----------

    @staticmethod
    def _load_voice_settings(config_path: str | None) -> tuple[VoiceSettings, Path]:
        try:
            cfg = load_config(config_path)
            return cfg.voice, cfg.data_dir
        except Exception:
            return VoiceSettings(), Path(".data")

    def _on_voice(self) -> None:
        if self._busy:
            self._set_status("这轮跑完再说…")
            return
        if self._voice_listening:
            self._set_status("正在录音，说完停一下就会自动结束")
            return
        self._pause_wake()
        self._voice_listening = True
        self.voice_btn.setText("在听…")
        self._voice.listen()

    def _on_wake(self, word: str) -> None:
        """常听唤醒命中：先应一声，再开始听指令。

        不应答的话用户不知道它醒没醒——喊完半天没动静最难受。
        应答语要短（两个字），合成快，不会让人等太久。
        """
        if self._busy or self._voice_listening:
            return

        self._set_status("Boss，我在")
        if self._orb.isVisible():
            self._orb.set_state("happy")
            self._orb.bubble("Boss，我在", "说吧")

        if self._tts_enabled:
            # 先播应答，播完再开麦：不然麦克风会把「Boss，我在」自己录进去
            self._pending_listen = True
            self._pause_wake()
            self._tts.speak(WAKE_REPLY)
        else:
            self._on_voice()

    def _pause_wake(self) -> None:
        if self._wake is not None:
            self._wake.disable()

    def _resume_wake(self) -> None:
        if self._wake is not None and not self._busy and not self._voice_listening:
            self._wake.enable()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """空格键当说话键：输入框空着时按空格开始录音。

        输入框里有字就照常打空格——不然聊天里一个空格都打不出来。
        """
        if obj is self.input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space and not self.input.text().strip():
                if not self._busy and not self._voice_listening:
                    self._on_voice()
                    return True
        return super().eventFilter(obj, event)

    def _on_voice_state(self, state: str) -> None:
        if state == "listening":
            self._set_status("在听… 说完停一下就行")
            self._set_state("listening")
        elif state == "transcribing":
            self.voice_btn.setText("识别中")
            self._set_status("识别中…")
        else:
            self._voice_listening = False
            self.voice_btn.setText("语音")
            if not self._busy:
                self._set_state("idle")
                self._set_status("")
            self._resume_wake()

    def _on_voice_text(self, text: str) -> None:
        hit, command = match_wake(text, self._voice_settings.wake_word)
        spoken = (command if hit else text).strip()
        self._enter_chat()
        self.chat.add_note(f"🎙 {'（唤醒）' if hit else ''}{text}")
        if not spoken:
            self._set_status("只听到了唤醒词，没听清要做什么")
            return
        self._send_text(spoken)

    def _on_voice_failed(self, message: str) -> None:
        self._voice_listening = False
        self.voice_btn.setText("语音")
        self._set_status(message)
        self._resume_wake()

    # ---------- 档位 ----------

    def _open_mode_menu(self) -> None:
        if self._busy:
            return
        menu = QMenu(self)
        menu.setStyleSheet(theme.MENU_QSS)
        rows = self._modes or [
            {"id": "chat", "label": "聊天"},
            {"id": "desktop", "label": "桌面"},
            {"id": "work", "label": "工作"},
        ]
        for row in rows:
            action = menu.addAction(f"{row['label']}（{row['id']}）")
            action.setCheckable(True)
            action.setChecked(row["id"] == self._mode)
            action.triggered.connect(
                lambda _=False, m=row["id"]: self._switch_mode(m)
            )
        menu.exec(self.mode_chip.mapToGlobal(QPoint(0, self.mode_chip.height() + 4)))

    def _switch_mode(self, mode: str) -> None:
        if mode == self._mode or self._busy or self._switching:
            return
        self._switching = True
        self._set_status(f"切换到 {mode} 档…")
        self._bridge.set_mode(mode)

    def _on_mode_changed(self, mode: str, model_id: str) -> None:
        self._mode = mode
        self._switching = False
        self._refresh_mode_chip()
        self._set_status("")
        # 不清空对话：切断上下文的是 agent，界面没必要把用户刚说的话抹掉
        if self._started_chat:
            self.chat.add_note(
                f"已切换到{self._mode_label(mode)}档 · {model_id}（上下文已重置）"
            )

    # ---------- 模型 ----------

    def _on_models_ready(self, rows: list) -> None:
        self._models = list(rows)

    def _on_keys_ready(self, rows: list) -> None:
        self._key_rows = list(rows)

    def _open_model_menu(self) -> None:
        if self._busy:
            self._set_status("这轮跑完再换模型…")
            return
        menu = self._build_model_menu()
        menu.exec(self.model_label.mapToGlobal(QPoint(0, self.model_label.height() + 4)))

    def _build_model_menu(self) -> QMenu:
        """把菜单拼出来但不弹——这样它可以在没有事件循环的地方被检查。"""
        menu = QMenu(self)
        menu.setStyleSheet(theme.MENU_QSS)

        if not self._models:
            hint = menu.addAction("（模型清单还没就绪）")
            hint.setEnabled(False)
        else:
            group = ""
            for row in self._models:
                label = "本地" if row["kind"] == "local_gguf" else "云端"
                if label != group and group:
                    menu.addSeparator()
                group = label

                if row["free"]:
                    suffix = "不计费（本机）"
                else:
                    suffix = f"¥{row['price_in']:g} / ¥{row['price_out']:g} 每百万"

                if row.get("has_key") is False:
                    text = f"{row['id']}   {label} · {suffix} · 未配置 Key"
                else:
                    text = f"{row['id']}   {label} · {suffix}"

                action = menu.addAction(text)
                action.setCheckable(True)
                action.setChecked(bool(row["active"]))
                if row.get("has_key") is False:
                    # 没 Key 就切过去也没用，直接引导去配
                    action.triggered.connect(
                        lambda _=False, ref=row["key_ref"]: self._open_key_dialog(ref)
                    )
                else:
                    action.triggered.connect(
                        lambda _=False, mid=row["id"]: self._switch_model(mid)
                    )

        menu.addSeparator()
        menu.addAction("设置 API Key…").triggered.connect(
            lambda: self._open_key_dialog("")
        )
        speech = menu.addAction("语音播报：开" if self._tts_enabled else "语音播报：关")
        speech.triggered.connect(self._toggle_tts)
        menu.addAction("选择音色…").triggered.connect(self._open_voice_dialog)
        menu.addAction("切换档位…").triggered.connect(self._open_mode_menu)
        menu.addAction("查看本次花费").triggered.connect(self._show_cost)
        return menu

    def _toggle_tts(self) -> None:
        self._tts_enabled = not self._tts_enabled
        self._set_status("语音播报已打开" if self._tts_enabled else "语音播报已关闭")

    def _open_voice_dialog(self) -> None:
        if self._busy:
            self._set_status("这轮跑完再换音色…")
            return
        voices = list_voice_models(Path(self._voice_settings.tts_model).parent)
        if not voices:
            self._set_status("models/voice 下没有可用音色")
            return
        dialog = VoiceDialog(
            voices, self._voice_settings.tts_model, self._tts.preview, self
        )
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = dialog.chosen()
        if not chosen:
            return
        self._voice_settings.tts_model = chosen["path"]
        self._tts.set_model(chosen["path"])
        save_voice_preference(self._data_dir, tts_model=chosen["path"], tts_speaker=0)
        self._set_status(f"音色已换成「{chosen['label']}」，已记住")

    def _open_key_dialog(self, preselect: str = "") -> None:
        if self._busy:
            self._set_status("这轮跑完再改 Key…")
            return
        if not self._key_rows:
            self._set_status("还没有需要 Key 的云端模型")
            return

        dialog = KeyDialog(self._key_rows, self, preselect)
        if dialog.exec() != QDialog.Accepted:
            return

        key_id = dialog.key_id()
        if dialog.action == "delete":
            self._bridge.delete_key(key_id)
            return
        secret = dialog.secret()
        if secret:
            self._set_status(f"正在保存 {key_id} 的 Key…")
            self._bridge.set_key(key_id, secret)

    def _switch_model(self, model_id: str) -> None:
        if self._busy:
            return
        self._set_status(f"切换到 {model_id}…")
        self._bridge.set_model(model_id)

    def _show_cost(self) -> None:
        usage = self._last_usage
        if not usage:
            self._set_status("本次会话还没调用过模型")
            return
        prompt = int(usage.get("session_prompt") or 0)
        completion = int(usage.get("session_completion") or 0)
        cost = float(usage.get("session_cost") or 0)
        text = f"会话 {prompt + completion} tokens（入 {prompt} / 出 {completion}）"
        text += f" · {format_money(cost)}" if cost > 0 else " · 本地模型不计费"
        self._set_status(text)

    def _update_cost(self, data: dict) -> None:
        self._last_usage = dict(data)
        if data.get("call_kind") == "summary":
            return  # 摘要的用量只进累计，不代表"这一次回答"
        self._session_cost = float(data.get("session_cost") or 0)
        self.cost_label.setText(
            format_money(self._session_cost) if self._session_cost > 0 else ""
        )

    # ---------- 上下文预算 / 步数 ----------

    def _on_limits_changed(self, setting: int, effective: int, steps: int) -> None:
        self._ctx_setting = int(setting)
        self._ctx_effective = int(effective)
        self._steps = int(steps)
        if self._ctx_setting == 0:
            self.ctx_chip.setText(f"ctx {self._ctx_effective / 1000:.1f}k")
        else:
            self.ctx_chip.setText(f"ctx {self._ctx_effective // 1000}k")
        self.ctx_chip.setToolTip(
            f"上下文预算 {self._ctx_effective} tokens"
            f"（{'自动' if self._ctx_setting == 0 else '手动'}）· 步数上限 {self._steps} 步"
        )

    def _open_limits_menu(self) -> None:
        if self._busy:
            self._set_status("这轮跑完再改设置…")
            return
        menu = self._build_limits_menu()
        menu.exec(self.ctx_chip.mapToGlobal(QPoint(0, self.ctx_chip.height() + 4)))

    def _build_limits_menu(self) -> QMenu:
        """预算与步数菜单：跟模型菜单一样，拼出来不弹，方便检查。"""
        menu = QMenu(self)
        menu.setStyleSheet(theme.MENU_QSS)

        head = menu.addAction("上下文预算")
        head.setEnabled(False)
        for value, text in (
            (0, "自动（按模型窗口）"),
            (4000, "4k tokens"),
            (8000, "8k tokens"),
            (16000, "16k tokens"),
            (24000, "24k tokens"),
        ):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(self._ctx_setting == value)
            action.triggered.connect(lambda _=False, v=value: self._apply_context(v))

        menu.addSeparator()
        head = menu.addAction("步数上限")
        head.setEnabled(False)
        for value in (6, 12, 20, 30):
            action = menu.addAction(f"{value} 步")
            action.setCheckable(True)
            action.setChecked(self._steps == value)
            action.triggered.connect(lambda _=False, v=value: self._apply_steps(v))
        return menu

    def _apply_context(self, tokens: int) -> None:
        self._bridge.set_context_limit(tokens)

    def _apply_steps(self, steps: int) -> None:
        self._bridge.set_max_steps(steps)

    def _metrics_text(self) -> str:
        """状态栏那行小字：耗时 · token · 花费。"""
        usage = self._last_usage
        if not usage:
            return ""
        total = int(usage.get("prompt_tokens") or 0) + int(
            usage.get("completion_tokens") or 0
        )
        bits: list[str] = []
        if self._last_latency:
            bits.append(f"{self._last_latency / 1000:.1f}s")
        if total:
            bits.append(f"{total} tokens")
        cost = float(usage.get("cost") or 0)
        if cost > 0:
            bits.append(format_money(cost))
        return "  ·  ".join(bits)

    # ---------- 工具确认 ----------

    def _on_confirm_required(self, name: str, args: dict) -> None:
        if os.environ.get("EVE_AUTO_CONFIRM") == "1":
            self.chat.add_note(f"已自动允许 {name}（测试模式）")
            self._bridge.answer_confirm(True)
            return

        self._pending_confirm = {"name": name, "args": dict(args)}
        preview = "，".join(f"{k}={v}" for k, v in args.items()) or "（无参数）"
        if len(preview) > 220:
            preview = preview[:220] + "…"

        # agent 干活时主窗已经收成球了，许可就贴到球上，别再把窗口弹回来
        if self._orb.isVisible():
            self._orb.set_state("thinking")
            self._orb.ask_confirm(name, preview)
        else:
            self.confirm_card.show_for(name, args)

    def _resolve_pending(self, allowed: bool) -> None:
        pending = self._pending_confirm
        if not pending:
            return
        self._pending_confirm = None
        self.confirm_card.hide()
        self._orb.cancel_confirm()
        name = pending["name"]
        self.chat.add_note(f"{'已允许' if allowed else '已拒绝'} {name}")
        self._bridge.answer_confirm(bool(allowed))

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

    def _on_modes_ready(self, modes: list) -> None:
        # 新格式是 [{"id","label"}]，兼容老的 [str]
        self._modes = [
            m if isinstance(m, dict) else {"id": m, "label": m} for m in modes
        ]
        self._refresh_mode_chip()

    def _mode_label(self, mode_id: str) -> str:
        for row in self._modes:
            if row["id"] == mode_id:
                return str(row["label"])
        return mode_id

    def _refresh_mode_chip(self) -> None:
        self.mode_chip.setText(self._mode_label(self._mode))
        self.mode_chip.setToolTip(
            " / ".join(f"{row['label']}（{row['id']}）" for row in self._modes)
        )
        self._mode_aliases = mode_aliases(
            {row["id"]: row["label"] for row in self._modes}
        )

    def _on_event(self, kind: str, data: dict) -> None:
        self._mirror_to_orb(kind, data)
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
            self._tool_row = self.chat.add_tool(data.get("name", ""), data.get("args") or {})
        elif kind == "tool_end":
            ok = bool(data.get("ok"))
            self._set_status(f"{data.get('name')} {'完成' if ok else '失败'}")
            if self._tool_row is not None:
                self._tool_row.set_result(ok, str(data.get("result") or ""))
                self._tool_row = None
        elif kind == "context_compacting":
            self._set_state("thinking")
            self._set_status("整理上下文…")
        elif kind == "context_compacted":
            after = data.get("tokens_after")
            self._set_status(f"上下文 ≈{after} tokens" if after else "")
        elif kind == "usage":
            self._update_cost(data)
        elif kind == "settings":
            message = str(data.get("message") or "")
            if message:
                self._set_status(message)
        elif kind == "reply_done":
            self._last_latency = int(data.get("latency_ms") or 0)
        elif kind == "truncated":
            self._set_status("输出被截断，结果可能不完整")
        elif kind == "error":
            self._set_state("error")
            self._set_status("出错了")

    def _on_replied(self, text: str) -> None:
        # 干活时主窗是收起的。活干完了直接把界面拉回来，而不是在球上飘一句摘要——
        # 用户要看的是结果，不是提示。
        if self._collapsing:
            self._cancel_collapse()
        if self._orb.isVisible():
            self._orb.set_state("happy")
            self._restore_from_orb()

        if self._streaming:
            self.chat.end_stream(text)
            self._streaming = False
        else:
            self.chat.add_message("assistant", text)
        self._set_state("idle")
        self._set_status(self._metrics_text())
        self._set_busy(False)
        self._resume_wake()

        # 念出来。播报期间暂停唤醒，免得把自己说的「伊芙」当成唤醒词
        if self._tts_enabled and text.strip():
            self._pause_wake()
            self._tts.speak(text)

    def _on_tts_finished(self, ok: bool) -> None:
        self._resume_wake()
        if self._pending_listen:
            # 唤醒应答刚播完，这时候才开麦
            self._pending_listen = False
            self._on_voice()
            return
        if not self._busy:
            self._set_status(self._metrics_text())

    def _on_failed(self, message: str) -> None:
        self._streaming = False
        # 出错时也不能让用户对着一颗球，把界面拉回来
        if self._collapsing:
            self._cancel_collapse()
        if self._orb.isVisible():
            self._restore_from_orb()
        self._set_state("error")
        self.chat.add_message("system", message)
        self._set_status("")
        self._set_busy(False)
        self._resume_wake()

    # ---------- 悬浮球 ----------

    def _fade(self, widget, start: float, end: float, duration: int, on_done=None):
        """窗口透明度动画。半透明窗口真关掉一次再开，比尺寸动画稳。"""
        anim = QPropertyAnimation(widget, b"windowOpacity", self)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        if on_done is not None:
            anim.finished.connect(on_done)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        return anim

    def _collapse_to_orb(self) -> None:
        """agent 干活时收起主窗，只留一个球在桌面上。

        收得太快会像闪一下消失，所以留一段淡出 + 球淡入的过渡。
        """
        if self._orb.isVisible() or self._collapsing:
            return
        self._collapsing = True

        def show_orb() -> None:
            if not self._collapsing:  # 中途被打断（活干得太快）
                return
            self.hide()
            self.setWindowOpacity(1.0)
            self._orb.set_state("thinking")
            self._orb.show_at_edge()
            self._orb.setWindowOpacity(0.0)
            self._orb.bubble("开始执行", "点我回到主窗口")
            self._orb_anim = self._fade(self._orb, 0.0, 1.0, 280, self._finish_collapse)

        self._collapse_anim = self._fade(self, 1.0, 0.0, 320, show_orb)

    def _finish_collapse(self) -> None:
        self._orb.setWindowOpacity(1.0)
        self._collapsing = False

    def _cancel_collapse(self) -> None:
        """活干得太快：还没收完就该把界面拉回来。"""
        if not self._collapsing:
            return
        self._collapsing = False
        anim = getattr(self, "_collapse_anim", None)
        if anim is not None:
            anim.stop()
        self._orb.hide()
        self.setWindowOpacity(1.0)
        self.show()

    def _restore_from_orb(self) -> None:
        if not self._orb.isVisible():
            return
        self._orb.hide()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setWindowOpacity(0.0)
        self._fade(self, 0.0, 1.0, 220)

    def _quit_app(self) -> None:
        self._orb.hide()
        self.shutdown_agent()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    @staticmethod
    def _short_args(args: dict | None) -> str:
        if not args:
            return ""
        text = "，".join(f"{k}={v}" for k, v in args.items())
        return text if len(text) <= 40 else text[:40] + "…"

    def _mirror_to_orb(self, kind: str, data: dict) -> None:
        if not self._orb.isVisible():
            return
        if kind in ("thinking", "reply_chunk"):
            self._orb.set_state("thinking")
        elif kind == "tool_start":
            self._orb.set_state("acting")
            self._orb.bubble(f"正在 {data.get('name')}", self._short_args(data.get("args")))
        elif kind == "tool_end":
            ok = bool(data.get("ok"))
            self._orb.set_state("happy" if ok else "error")
            self._orb.bubble("这步完成" if ok else "这步失败了")
        elif kind == "error":
            self._orb.set_state("error")
            self._orb.bubble("出错了")
        elif kind == "engine_ready":
            self._orb.set_state("idle")

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

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.confirm_card.isVisible():
            self.confirm_card._reposition()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown_agent()
        super().closeEvent(event)

    def shutdown_agent(self) -> None:
        """退出前必须停线程，否则会报 QThread destroyed while running。"""
        try:
            self._voice.shutdown()
        except Exception:
            pass
        try:
            if self._wake is not None:
                self._wake.shutdown()
        except Exception:
            pass
        try:
            self._tts.shutdown()
        except Exception:
            pass
        try:
            self.confirm_card.cancel()
            self._orb.cancel_confirm()
        except Exception:
            pass
        try:
            self._bridge.shutdown()
        except Exception:
            pass
