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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .bridge import AgentBridge
from .chat_view import ChatView
from .mobius import MobiusWidget
from .orb import OrbWindow
from .tasks import MAX_RUNNING, Task
from .tts import TtsBridge
from .voice import VoiceBridge, WakeBridge
from eve_core.config import load_config, save_preference, save_voice_preference
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
        # 多任务：每个任务有自己的 AgentBridge 和聊天视图，切换只换视图
        self._tasks: dict[str, Task] = {}
        self._order: list[str] = []
        self._active_id = ""
        self._task_seq = 0

        self._build()
        self._config_path = config_path
        self.confirm_card = ConfirmCard(self)
        self.confirm_card.decided.connect(self._resolve_pending)
        self._orb = OrbWindow()
        self._orb.clicked.connect(self._restore_from_orb)
        self._orb.quit_requested.connect(self._quit_app)
        self._orb.confirm_answered.connect(self._resolve_pending)

        (
            self._voice_settings,
            self._data_dir,
            self._auto_confirm,
            self._mode_has_tools,
        ) = self._load_voice_settings(config_path)
        self._refresh_perm_chip()  # 顶栏那颗权限胶囊要反映记住的状态
        self._refresh_resume_btn()  # 上次有没做完的活就把「继续」亮出来
        self._voice = VoiceBridge(self._voice_settings, self)
        self._voice.state_changed.connect(self._on_voice_state)
        self._voice.transcript.connect(self._on_voice_text)
        self._voice.failed.connect(self._on_voice_failed)
        self._voice_listening = False
        self._collapsing = False  # 正在收成悬浮球（防重入）
        self._pending_listen = False  # 唤醒应答播完后接着开麦
        self._tts_streaming = False  # 这次回复正在边生成边播
        self._mode_aliases: dict[str, str] = {}

        self._wake = None
        if self._voice_settings.kws_enabled:
            self._wake = WakeBridge(self._voice_settings, self)
            self._wake.woke.connect(self._on_wake)
            self._wake.failed.connect(lambda msg: self._set_status(msg))

        self._tts_enabled = bool(self._voice_settings.tts_enabled)
        voice = self._voice_settings
        self._tts = TtsBridge(
            TtsSettings(
                model=voice.tts_model,
                speed=voice.tts_speed,
                speaker=voice.tts_speaker,
                enabled=self._tts_enabled,
                volume=voice.tts_volume,
                noise_scale=voice.tts_noise_scale,
                noise_scale_w=voice.tts_noise_scale_w,
                fx=voice.tts_fx,
                warmth=voice.tts_fx_warmth,
                clarity=voice.tts_fx_clarity,
                deharsh=voice.tts_fx_deharsh,
                space=voice.tts_fx_space,
                compression=voice.tts_fx_compression,
            ),
            self,
        )
        self._tts.started.connect(lambda: self._set_status("播报中…"))
        self._tts.finished.connect(self._on_tts_finished)
        self._tts.failed.connect(lambda msg: self._set_status(msg))
        if self._tts_enabled:
            # 提前把 VITS 加载好，第一句回复不用再等那 2 秒
            QTimer.singleShot(1500, self._tts.preload)

        self._wire()
        # 开第一个任务：多任务从这里长出来
        first = self._spawn_task(mode=self._mode)
        if first is not None:
            self._active_id = first.id
            self.chat_stack.setCurrentWidget(first.chat)
            self._refresh_task_bar()
            # 指定了 EVE_MODEL 时跳过本地预加载，避免白起一次本地引擎
            if not os.environ.get("EVE_MODEL"):
                first.bridge.ready.connect(lambda *_: first.bridge.preload())

    # ---------- 构建 ----------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        self.task_bar = self._build_task_bar()
        root.addWidget(self.task_bar)

        body = QVBoxLayout()
        body.setContentsMargins(SIDE, 4, SIDE, 0)
        body.setSpacing(0)

        self.hero = self._build_hero()
        body.addWidget(self.hero, 1)

        # 每个任务一个 ChatView：切任务只换 Stack 的当前页，滚动位置都保留
        self.chat_stack = QStackedWidget(self)
        self.chat_stack.hide()
        body.addWidget(self.chat_stack, 0)

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

    def _build_task_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setVisible(False)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(SIDE + 4, 2, SIDE + 4, 2)
        layout.setSpacing(6)
        self._task_chips_layout = layout
        self._task_chips: dict[str, QPushButton] = {}
        return bar

    def _refresh_task_bar(self) -> None:
        """任务数 > 1 才显示。chip 很少，整条重建比增量维护简单。"""
        layout = self._task_chips_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._task_chips = {}

        if len(self._order) <= 1:
            self.task_bar.setVisible(False)
            return

        self.task_bar.setVisible(True)
        for index, tid in enumerate(self._order, 1):
            task = self._tasks.get(tid)
            if task is None:
                continue
            chip = QPushButton(f"{task.icon} {index}. {task.short_title}", self.task_bar)
            chip.setFixedHeight(22)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(
                f"{task.title}\n状态：{task.status}"
                + (f"\n花费：¥{task.cost:.4f}" if task.cost else "")
                + "\n点一下切换，右键关闭"
            )
            chip.setStyleSheet(
                theme.PERM_CHIP_ON_QSS if tid == self._active_id else theme.MODE_CHIP_QSS
            )
            chip.clicked.connect(lambda _=False, target=tid: self._switch_task(target))
            chip.setContextMenuPolicy(Qt.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda _pos, target=tid, widget=chip: self._task_chip_menu(target, widget)
            )
            layout.addWidget(chip)
            self._task_chips[tid] = chip
        layout.addStretch(1)

    def _task_chip_menu(self, tid: str, widget: QWidget) -> None:
        task = self._tasks.get(tid)
        if task is None:
            return
        menu = QMenu(widget)
        menu.setStyleSheet(theme.MENU_QSS)
        menu.addAction("切到这个任务").triggered.connect(
            lambda: self._switch_task(tid)
        )
        menu.addAction("停止它").triggered.connect(
            lambda: task.bridge.cancel() if task.bridge else None
        )
        menu.addAction("关闭任务").triggered.connect(lambda: self._close_task(tid))
        menu.exec(widget.mapToGlobal(QPoint(0, widget.height() + 4)))

    @property
    def _task(self):
        return self._tasks.get(self._active_id)

    @property
    def chat(self):
        task = self._tasks.get(self._active_id)
        return task.chat if task else None

    @property
    def _bridge(self):
        task = self._tasks.get(self._active_id)
        return task.bridge if task else None

    def _spawn_task(self, mode: str | None = None, request: str = "") -> Task | None:
        """开一个新任务。超过并发上限就不开，返回 None。"""
        running = sum(1 for item in self._tasks.values() if item.busy)
        if running >= MAX_RUNNING:
            self._set_status(f"同时最多跑 {MAX_RUNNING} 个任务，先等一个跑完，或者停掉一个")
            return None

        self._task_seq += 1
        tid = f"task-{self._task_seq}"
        task = Task(
            id=tid,
            mode=mode or self._mode,
            request=request,
            title=request or f"任务 {self._task_seq}",
        )
        task.chat = ChatView(self)
        task.chat.hide()
        self.chat_stack.addWidget(task.chat)

        task.bridge = AgentBridge(self._config_path, self, autostart=False)
        self._wire_task(task)
        self._tasks[tid] = task
        self._order.append(tid)
        task.bridge.start()
        self._refresh_task_bar()
        return task

    def _wire_task(self, task: Task) -> None:
        tid = task.id
        bridge = task.bridge
        bridge.ready.connect(lambda mid, label, t=tid: self._on_ready(t, mid, label))
        bridge.event.connect(lambda kind, data, t=tid: self._on_event(t, kind, data))
        bridge.replied.connect(lambda text, t=tid: self._on_replied(t, text))
        bridge.failed.connect(lambda msg, t=tid: self._on_failed(t, msg))
        bridge.mode_changed.connect(
            lambda mode, mid, t=tid: self._on_mode_changed(t, mode, mid)
        )
        bridge.confirm_required.connect(
            lambda name, args, t=tid: self._on_confirm_required(t, name, args)
        )
        bridge.limits_changed.connect(
            lambda setting, effective, steps, t=tid: self._on_limits_changed(
                t, setting, effective, steps
            )
        )
        bridge.modes_ready.connect(self._on_modes_ready)
        bridge.models_ready.connect(self._on_models_ready)
        bridge.keys_ready.connect(self._on_keys_ready)

    def _switch_task(self, tid: str) -> None:
        if tid == self._active_id or tid not in self._tasks:
            return
        self._active_id = tid
        task = self._tasks[tid]
        self.chat_stack.setCurrentWidget(task.chat)
        self._sync_active_ui()
        self._refresh_task_bar()

    def _sync_active_ui(self) -> None:
        """切任务时把顶栏和输入区同步成这个任务的状态。"""
        task = self._task
        if task is None:
            return
        self._mode = task.mode
        self._refresh_mode_chip()
        if task.model:
            label = next(
                (str(row.get("label") or row.get("id")) for row in self._models
                 if row.get("id") == task.model),
                task.model,
            )
            self.model_label.setText(label)
        self._update_cost_from(task)
        self._set_busy(task.busy)
        if self._started_chat:
            self.chat_stack.show()
            self.hero.hide()

    def _close_task(self, tid: str) -> None:
        task = self._tasks.get(tid)
        if task is None:
            return
        try:
            task.bridge.shutdown()
        except Exception:
            pass
        self.chat_stack.removeWidget(task.chat)
        task.chat.deleteLater()
        self._tasks.pop(tid, None)
        if tid in self._order:
            self._order.remove(tid)
        if self._active_id == tid:
            self._active_id = self._order[-1] if self._order else ""
            task = self._task
            if task is not None:
                self.chat_stack.setCurrentWidget(task.chat)
                self._sync_active_ui()
            else:
                self._spawn_task()
        self._refresh_task_bar()

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

        # 一键权限：点一下在"逐次询问"和"完全权限"之间切。状态会存进 .data/prefs.json
        self.perm_chip = QPushButton("权限", bar)
        self.perm_chip.setFixedHeight(20)
        self.perm_chip.setCursor(Qt.PointingHandCursor)
        self.perm_chip.setStyleSheet(theme.PERM_CHIP_QSS)
        self.perm_chip.clicked.connect(self._toggle_auto_confirm)
        layout.addWidget(self.perm_chip, 0, Qt.AlignVCenter)
        layout.addSpacing(4)

        # 新任务：后台任务不会被它打断，切过去就是一份干净的上下文
        self.new_task_btn = QPushButton("＋", bar)
        self.new_task_btn.setFixedSize(26, 20)
        self.new_task_btn.setCursor(Qt.PointingHandCursor)
        self.new_task_btn.setStyleSheet(theme.MODE_CHIP_QSS)
        self.new_task_btn.setToolTip("开一个新任务（Ctrl+T）。正在跑的任务会继续跑。")
        self.new_task_btn.clicked.connect(self._new_task_clicked)
        layout.addWidget(self.new_task_btn, 0, Qt.AlignVCenter)
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
        self.send_btn.clicked.connect(self._on_send_clicked)

        self.voice_btn = QPushButton("语音", self)
        self.voice_btn.setCursor(Qt.PointingHandCursor)
        self.voice_btn.setStyleSheet(theme.VOICE_BUTTON_QSS)
        self.voice_btn.setMinimumHeight(44)
        self.voice_btn.setFixedWidth(72)
        self.voice_btn.setToolTip("点一下开始说，说完停一下自动结束（空格 / Ctrl+Space）")
        self.voice_btn.clicked.connect(self._on_voice)

        # 上次有没做完的活时才出现：点了从断点继续，不用重讲一遍需求
        self.resume_btn = QPushButton("继续", self)
        self.resume_btn.setCursor(Qt.PointingHandCursor)
        self.resume_btn.setStyleSheet(theme.VOICE_BUTTON_QSS)
        self.resume_btn.setMinimumHeight(44)
        self.resume_btn.setFixedWidth(64)
        self.resume_btn.setVisible(False)
        self.resume_btn.setToolTip("接着上次没做完的任务继续（Ctrl+R），右键看断点列表")
        self.resume_btn.clicked.connect(lambda _checked=False: self._resume_task())
        self.resume_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.resume_btn.customContextMenuRequested.connect(
            lambda _pos: self._open_resume_menu()
        )

        row.addWidget(self.input, 1)
        row.addWidget(self.resume_btn)
        row.addWidget(self.voice_btn)
        row.addWidget(self.send_btn)
        return row

    # ---------- 接线 ----------

    def _wire(self) -> None:
        # 每个任务的 bridge 在 _wire_task 里单独接线（多任务各连各的），
        # 这里只剩全局快捷键。
        QShortcut(QKeySequence("Ctrl+Space"), self, activated=self._on_voice)
        QShortcut(QKeySequence("Esc"), self, activated=self._stop_task)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._resume_task)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self._new_task_clicked)

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
        task = self._task
        if task is not None:
            if not task.request:
                # 第一条消息就是这个任务的名字
                task.request = text
                task.title = text
                self._refresh_task_bar()
            task.set_status("running")
        self._set_busy(True)
        self._pause_wake()
        # 上一轮还在念就别念了：新问题已经开口，旧回答继续播只是噪音
        self._tts_streaming = False
        self._tts.cancel()
        self._set_status("思考中…")
        self._set_state("thinking")
        self._bridge.ask(text)
        # 有工具的档位（桌面 / 工作）都收成悬浮球，纯聊天档不收。
        # 以前写死成只收桌面档，所以切到工作档就没球了。
        if self._mode_has_tools.get(self._mode, False):
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
    def _load_voice_settings(
        config_path: str | None,
    ) -> tuple[VoiceSettings, Path, bool, dict[str, bool]]:
        try:
            cfg = load_config(config_path)
            has_tools = {name: bool(mode.tools) for name, mode in cfg.modes.items()}
            return cfg.voice, cfg.data_dir, bool(cfg.auto_confirm), has_tools
        except Exception:
            return (
                VoiceSettings(),
                Path(".data"),
                False,
                {"chat": False, "desktop": True, "work": True},
            )

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

    def _on_mode_changed(self, tid: str, mode: str, model_id: str) -> None:
        task = self._tasks.get(tid)
        if task is not None:
            task.mode = mode
            task.model = model_id
        if tid != self._active_id:
            return
        self._mode = mode
        self._switching = False
        self._refresh_mode_chip()
        self._set_status("")
        # 球还开着的话，把档位名一并换掉，不然看着像没切过去
        if self._orb.isVisible():
            self._orb.bubble(f"{self._mode_label(mode)}档", model_id)
        # 不清空对话：切断上下文的是 agent，界面没必要把用户刚说的话抹掉
        if self._started_chat and self.chat is not None:
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
        root = menu.addAction(
            "完全权限：开（不再询问）" if self._auto_confirm else "完全权限：关（逐次询问）"
        )
        root.setCheckable(True)
        root.setChecked(self._auto_confirm)
        root.triggered.connect(self._toggle_auto_confirm)
        menu.addAction("切换档位…").triggered.connect(self._open_mode_menu)
        menu.addAction("查看本次花费").triggered.connect(self._show_cost)
        return menu

    def _toggle_auto_confirm(self) -> None:
        """完全权限：读写文件、跑命令、生成文件一律不再弹确认。"""
        self._auto_confirm = not self._auto_confirm
        self._bridge.set_auto_confirm(self._auto_confirm)
        self._refresh_perm_chip()
        try:
            save_preference(self._data_dir, auto_confirm=self._auto_confirm)
        except Exception:
            pass  # 存不下来不影响本次生效
        note = (
            "完全权限已打开：接下来读写文件、跑命令、生成文件都不会再问你"
            if self._auto_confirm
            else "完全权限已关闭：高风险操作恢复确认"
        )
        if self._started_chat:
            self.chat.add_note(note)
        self._set_status(note)

    def _refresh_perm_chip(self) -> None:
        chip = getattr(self, "perm_chip", None)
        if chip is None:
            return
        if self._auto_confirm:
            chip.setText("权限：完全")
            chip.setStyleSheet(theme.PERM_CHIP_ON_QSS)
            chip.setToolTip(
                "完全权限已打开：写文件、跑命令、生成文档都不再询问。\n"
                "点一下改回「逐次确认」。这个选择会记住。"
            )
        else:
            chip.setText("权限：询问")
            chip.setStyleSheet(theme.PERM_CHIP_QSS)
            chip.setToolTip(
                "逐次确认：中高风险操作（写文件、跑命令、生成文档）会先问你。\n"
                "点一下切到「完全权限」，长任务不再被打断。"
            )

    def _toggle_tts(self) -> None:
        self._tts_enabled = not self._tts_enabled
        if not self._tts_enabled:
            # 关掉就当场闭嘴，别让已经排好的句子继续念
            self._tts_streaming = False
            self._tts.cancel()
            self._resume_wake()
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
        task = self._task
        if task is not None:
            task.last_usage = dict(data)
            task.cost = float(data.get("session_cost") or 0)
        self._last_usage = dict(data)
        if data.get("call_kind") == "summary":
            return  # 摘要的用量只进累计，不代表"这一次回答"
        self._session_cost = float(data.get("session_cost") or 0)
        self.cost_label.setText(
            format_money(self._session_cost) if self._session_cost > 0 else ""
        )

    def _update_cost_from(self, task) -> None:
        """切任务时把花费显示换成这个任务的账。"""
        self._last_usage = dict(task.last_usage)
        self._session_cost = float(task.cost)
        self.cost_label.setText(
            format_money(self._session_cost) if self._session_cost > 0 else ""
        )

    # ---------- 上下文预算 / 步数 ----------

    def _on_limits_changed(
        self, tid: str, setting: int, effective: int, steps: int
    ) -> None:
        if tid != self._active_id:
            return
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

    def _on_confirm_required(self, tid: str, name: str, args: dict) -> None:
        task = self._tasks.get(tid)
        if os.environ.get("EVE_AUTO_CONFIRM") == "1":
            if task is not None and task.chat is not None:
                task.chat.add_note(f"已自动允许 {name}（测试模式）")
            if task is not None and task.bridge is not None:
                task.bridge.answer_confirm(True)
            return

        self._pending_confirm = {"tid": tid, "name": name, "args": dict(args)}
        preview = "，".join(f"{k}={v}" for k, v in args.items()) or "（无参数）"
        if len(preview) > 220:
            preview = preview[:220] + "…"

        # 后台任务的确认：只弹卡片，不把窗口拉回来
        active = tid == self._active_id
        if active and self._orb.isVisible():
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
        tid = pending.get("tid") or self._active_id
        task = self._tasks.get(tid)
        if task is not None and task.chat is not None:
            task.chat.add_note(f"{'已允许' if allowed else '已拒绝'} {name}")
        if task is not None and task.bridge is not None:
            task.bridge.answer_confirm(bool(allowed))

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
        self.chat_stack.show()
        chat_effect = QGraphicsOpacityEffect(self.chat_stack)
        self.chat_stack.setGraphicsEffect(chat_effect)
        chat_fade = QPropertyAnimation(chat_effect, b"opacity", self)
        chat_fade.setDuration(280)
        chat_fade.setStartValue(0.0)
        chat_fade.setEndValue(1.0)
        chat_fade.setEasingCurve(QEasingCurve.OutCubic)
        chat_fade.finished.connect(lambda: self.chat_stack.setGraphicsEffect(None))
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

    def _set_busy(self, busy: bool, tid: str | None = None) -> None:
        tid = tid or self._active_id
        task = self._tasks.get(tid)
        if task is not None:
            task.busy = busy
            task.touch()
        if tid != self._active_id:
            # 后台任务忙不忙，只影响它自己的 chip，不动输入区
            self._refresh_task_bar()
            return
        self._busy = busy
        self.input.setEnabled(not busy)
        # 忙的时候这颗按钮变成"停止"：长任务跑到一半总得有个地方能叫停
        self.send_btn.setText("停止" if busy else "发送")
        self.send_btn.setEnabled(True)  # 一直可点：闲着是发送，忙着是停止
        if not busy:
            self.input.setFocus()
        self._refresh_task_bar()

    def _on_send_clicked(self) -> None:
        if self._busy:
            self._stop_task()
        else:
            self._send()

    def _new_task_clicked(self) -> None:
        """开一个新任务并切过去。后台任务继续跑，不受影响。"""
        task = self._spawn_task(mode=self._mode)
        if task is None:
            return
        self._switch_task(task.id)
        self._set_status("新任务：说句话就开始")
        self.input.setFocus()

    def _stop_task(self) -> None:
        """叫停当前任务。agent 会在这一步结束后退出循环。"""
        if not self._busy:
            return
        self._bridge.cancel()
        self._tts_streaming = False
        self._tts.cancel()
        self._set_status("正在停止…（当前这一步跑完就停）")
        self._set_state("idle")

    def _resume_task(self, task_id: str | None = None) -> None:
        """接着上次没做完的活：上下文和计划由 agent 自己捞回来。"""
        if self._busy:
            return
        self._enter_chat()
        self._set_busy(True)
        self._pause_wake()
        self._tts_streaming = False
        self._tts.cancel()
        self._set_status("接着上次的活…")
        self._set_state("thinking")
        self._bridge.resume(task_id)

    def _open_resume_menu(self) -> None:
        """断点列表：存下来的每个任务都能挑一个继续。"""
        from eve_agent.checkpoint import list_all

        items = list_all(self._data_dir)
        menu = QMenu(self)
        menu.setStyleSheet(theme.MENU_QSS)
        if not items:
            action = menu.addAction("没有保存的断点")
            action.setEnabled(False)
        for item in items:
            title = (item.request or item.id)[:26]
            label = f"{title}　{item.done_steps}/{len(item.todo)} 步 · {item.updated[:16]}"
            menu.addAction(label).triggered.connect(
                lambda _checked=False, cid=item.id: self._resume_task(cid)
            )
        menu.exec(self.resume_btn.mapToGlobal(QPoint(0, -6 - 26 * len(items) - 8)))

    def _refresh_resume_btn(self) -> None:
        """有断点才显示「继续」。每次任务结束/中断都刷一次。"""
        button = getattr(self, "resume_btn", None)
        if button is None:
            return
        items: list = []
        try:
            from eve_agent.checkpoint import list_all

            items = list_all(self._data_dir)
        except Exception:
            items = []

        if not items:
            button.setVisible(False)
            return

        snapshot = items[0]
        bits: list[str] = []
        if snapshot.request:
            bits.append(snapshot.request[:26])
        if snapshot.todo:
            bits.append(f"{snapshot.done_steps}/{len(snapshot.todo)} 步")
        if snapshot.files:
            bits.append(f"{len(snapshot.files)} 个文件")
        button.setVisible(True)
        more = f"（还有 {len(items) - 1} 个断点，右键看列表）" if len(items) > 1 else ""
        button.setToolTip(
            "接着上次没做完的任务继续（Ctrl+R）\n" + " · ".join(bits) + more
        )

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

    def _on_ready(self, tid: str, model_id: str, label: str) -> None:
        task = self._tasks.get(tid)
        if task is not None:
            task.model = model_id
        if tid == self._active_id:
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

    def _on_event(self, tid: str, kind: str, data: dict) -> None:
        task = self._tasks.get(tid)
        if task is None:
            return
        active = tid == self._active_id
        chat = task.chat
        status_changed = False

        if active:
            self._mirror_to_orb(kind, data)

        if kind == "engine_starting":
            task.set_status("running")
            status_changed = True
            if active:
                self._set_state("boot")
                self._set_status("加载模型…")
        elif kind == "engine_ready":
            if active:
                self._set_state("idle")
                seconds = data.get("load_seconds")
                self._set_status(f"就绪 {seconds}s" if seconds else "就绪")
        elif kind == "engine_stopped":
            if active:
                self._set_state("muted")
                self._set_status("引擎已停止")
        elif kind == "thinking":
            task.set_status("running")
            status_changed = True
            if active:
                step = data.get("step")
                self._set_state("thinking")
                self._set_status(f"思考中…（第 {step} 步）" if step else "思考中…")
        elif kind == "reply_chunk":
            if not task.streaming:
                task.streaming = True
                if active:
                    self._set_state("streaming")
                    self._set_status("")
            delta = data.get("text", "")
            chat.push_chunk(delta)
            if active:
                self._feed_speech(delta)
        elif kind == "tool_start":
            task.set_status("running")
            status_changed = True
            task.tool_row = chat.add_tool(data.get("name", ""), data.get("args") or {})
            if active:
                self._set_state("acting")
                self._set_status(f"执行 {data.get('name')}…")
        elif kind == "tool_end":
            ok = bool(data.get("ok"))
            if task.tool_row is not None:
                task.tool_row.set_result(ok, str(data.get("result") or ""))
                task.tool_row = None
            if active:
                self._set_status(f"{data.get('name')} {'完成' if ok else '失败'}")
        elif kind == "context_compacting":
            if active:
                self._set_state("thinking")
                self._set_status("整理上下文…")
        elif kind == "context_compacted":
            if active:
                after = data.get("tokens_after")
                self._set_status(f"上下文 ≈{after} tokens" if after else "")
        elif kind == "usage":
            task.last_usage = dict(data)
            task.cost = float(data.get("session_cost") or 0)
            if active:
                self._update_cost(data)
        elif kind == "settings":
            message = str(data.get("message") or "")
            if active and message:
                self._set_status(message)
        elif kind == "reply_done":
            task.last_latency = int(data.get("latency_ms") or 0)
            if active:
                self._last_latency = task.last_latency
        elif kind == "truncated":
            if active:
                self._set_status("输出被截断，结果可能不完整")
        elif kind == "checkpoint_saved":
            if active:
                self._refresh_resume_btn()
            done = data.get("done")
            total = data.get("total")
            where = f"（{done}/{total} 步）" if total else ""
            chat.add_note(f"进度已存档{where} · 点「继续」接着做")
        elif kind == "task_resumed":
            chat.add_note("从上次的断点继续")
        elif kind == "error":
            task.set_status("error")
            status_changed = True
            if active:
                self._set_state("error")
                self._set_status("出错了")

        if status_changed:
            self._refresh_task_bar()

    def _feed_speech(self, delta: str) -> None:
        """模型每吐一段字就喂给播报：边生成边念，声音不落后于文字。

        以前是等整段回复出完才开念，回复越长落后越多——用户听到的第一句
        永远是"迟到的整段"。现在第一句一出标点就开始合成。
        """
        if not self._tts_enabled or not delta:
            return
        if not self._tts_streaming:
            self._tts_streaming = True
            self._pause_wake()  # 别把自己的播报当成唤醒词
            self._tts.stream_begin()
        self._tts.stream_text(delta)

    def _end_speech(self, text: str) -> bool:
        """回复结束：把没念完的收尾。返回是否真的会出声。"""
        streaming, self._tts_streaming = self._tts_streaming, False
        if not self._tts_enabled:
            if streaming:
                self._tts.cancel()  # 流到一半被关掉：会话还开着，得主动收
            return False
        # "已停止"这种系统口吻的话不用念出来
        if text.startswith("（已停止"):
            if streaming:
                self._tts.cancel()
            return False
        if streaming:
            self._tts.stream_end()
            return True
        if text.strip():
            self._pause_wake()
            self._tts.speak(text)
            return True
        return False

    def _on_replied(self, tid: str, text: str) -> None:
        task = self._tasks.get(tid)
        if task is None:
            return
        active = tid == self._active_id

        if task.streaming:
            task.chat.end_stream(text)
            task.streaming = False
        else:
            task.chat.add_message("assistant", text)
        task.set_status("done")
        self._refresh_task_bar()

        if not active:
            # 后台任务跑完了：不抢焦点，只在状态栏提一句
            self._set_status(f"「{task.short_title}」跑完了 · 点上面的任务条切过去看")
            return

        # 干活时主窗是收起的。活干完了直接把界面拉回来，而不是在球上飘一句摘要——
        # 用户要看的是结果，不是提示。
        if self._collapsing:
            self._cancel_collapse()
        if self._orb.isVisible():
            self._orb.set_state("happy")
            self._restore_from_orb()

        self._set_state("idle")
        self._set_status(self._metrics_text())
        self._set_busy(False, tid)

        # 念出来。播报期间暂停唤醒，免得把自己说的「伊芙」当成唤醒词。
        # 要开播就别先唤醒：醒来又立刻暂停，多一次开关麦克风还可能误触发。
        if not self._end_speech(text):
            self._resume_wake()
        self._refresh_resume_btn()

    def _on_tts_finished(self, ok: bool) -> None:
        self._resume_wake()
        if self._pending_listen:
            # 唤醒应答刚播完，这时候才开麦
            self._pending_listen = False
            self._on_voice()
            return
        if not self._busy:
            self._set_status(self._metrics_text())

    def _on_failed(self, tid: str, message: str) -> None:
        task = self._tasks.get(tid)
        if task is None:
            return
        active = tid == self._active_id
        task.streaming = False
        task.set_status("error")
        self._refresh_task_bar()
        task.chat.add_message("system", message)

        if not active:
            self._set_status(f"「{task.short_title}」出错了 · 点任务条切过去看")
            return

        # 出错就闭嘴，别让半句话继续念下去
        self._tts_streaming = False
        self._tts.cancel()
        # 出错时也不能让用户对着一颗球，把界面拉回来
        if self._collapsing:
            self._cancel_collapse()
        if self._orb.isVisible():
            self._restore_from_orb()
        self._set_state("error")
        self._set_status("")
        self._set_busy(False, tid)
        self._resume_wake()
        self._refresh_resume_btn()

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

        # 兜底：动画回调万一没来（窗口被别的操作打断、被最小化等），
        # 到点强制解除，否则之后再也不收球了——表现就是"悬浮球没了"。
        QTimer.singleShot(900, self._reset_collapsing)

        def show_orb() -> None:
            if not self._collapsing:  # 中途被打断（活干得太快）
                return
            self.hide()
            self.setWindowOpacity(1.0)
            self._orb.set_state("thinking")
            self._orb.show_at_edge()
            self._orb.setWindowOpacity(0.0)
            self._orb.bubble(f"{self._mode_label(self._mode)}档 · 开始执行", "点我回到主窗口")
            self._orb_anim = self._fade(self._orb, 0.0, 1.0, 280, self._finish_collapse)

        self._collapse_anim = self._fade(self, 1.0, 0.0, 320, show_orb)

    def _reset_collapsing(self) -> None:
        if self._collapsing and self._orb.isVisible():
            self._orb.setWindowOpacity(1.0)
        self._collapsing = False

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
            for task in list(self._tasks.values()):
                try:
                    task.bridge.shutdown()
                except Exception:
                    pass
        except Exception:
            pass
