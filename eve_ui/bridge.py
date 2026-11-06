"""把 agent 接到 Qt 上。

关键约束：Agent 会阻塞（加载模型、等模型吐字、等用户确认），所以它必须活在子线程里；
所有界面绘制只在主线程。事件通过 Signal 跨线程，Qt 自动排队。

工具确认的流程比较绕，单独说明：
  子线程（执行工具） --signal--> 主线程（弹窗）
  子线程 --Event.wait()-- 阻塞（此时它的事件循环是停的）
  主线程 --直接调用 resolve_confirm()--> 放行

最后一步**不能走信号**：worker 阻塞时事件循环没在跑，
排在它队列里的槽函数永远执行不到，会死锁。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread, Signal, Slot

from eve_agent import Agent
from eve_core.config import load_config
from eve_core.events import EventBus


class AgentWorker(QObject):
    event = Signal(str, dict)      # kind, data
    ready = Signal(str, str)       # model_id, label
    replied = Signal(str)
    failed = Signal(str)
    confirm_required = Signal(str, dict)   # 工具名、参数
    mode_changed = Signal(str, str)        # mode, model_id
    modes_ready = Signal(list)
    finished = Signal()

    def __init__(self, config_path: str | None = None):
        super().__init__()
        self._config_path = config_path
        self._agent: Agent | None = None
        self._decision = threading.Event()
        self._allowed = False

    @Slot()
    def start(self) -> None:
        try:
            cfg = load_config(self._config_path)
            bus = EventBus()
            bus.subscribe(self._on_event)
            self._agent = Agent(cfg, cfg.default_mode, bus=bus)
            self.modes_ready.emit(sorted(cfg.modes))
            self.ready.emit(self._agent.model_id, cfg.model(self._agent.model_id).label)
        except Exception as exc:  # 配置错误也要让界面显示出来
            self.failed.emit(str(exc))

    def _on_event(self, ev) -> None:
        # 这里在子线程执行，emit 会自动排队到主线程
        self.event.emit(ev.kind, ev.data)

    # ---------- 工具确认 ----------

    def _confirm(self, spec, tool_args) -> bool:
        self._allowed = False
        self._decision.clear()
        self.confirm_required.emit(spec.name, dict(tool_args))
        if not self._decision.wait(timeout=180):
            return False
        return self._allowed

    def resolve_confirm(self, allowed: bool) -> None:
        """由主线程直接调用。

        不能走信号：此刻 worker 正阻塞在 Event.wait() 上，事件循环没在跑，
        排给它的槽函数永远执行不到（会死锁）。
        """
        self._allowed = bool(allowed)
        self._decision.set()

    # ---------- 任务 ----------

    @Slot()
    def preload(self) -> None:
        """提前把模型拉起来，避免用户第一次发消息干等。"""
        if self._agent is None:
            return
        try:
            _ = self._agent.provider
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot(str)
    def ask(self, text: str) -> None:
        if self._agent is None:
            self.failed.emit("agent 尚未就绪")
            return
        try:
            self.replied.emit(self._agent.send(text, confirm=self._confirm))
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot(str)
    def set_mode(self, mode_name: str) -> None:
        if self._agent is None:
            return
        try:
            self._agent.switch_mode(mode_name)
            self.mode_changed.emit(self._agent.mode_name, self._agent.model_id)
            self.ready.emit(
                self._agent.model_id, self._agent.cfg.model(self._agent.model_id).label
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot()
    def shutdown(self) -> None:
        if self._agent is not None:
            try:
                self._agent.runtime.shutdown()
            except Exception:
                pass
            self._agent = None
        self.finished.emit()


class AgentBridge(QObject):
    """主线程侧的门面：持有线程，转发信号。"""

    event = Signal(str, dict)
    ready = Signal(str, str)
    replied = Signal(str)
    failed = Signal(str)
    confirm_required = Signal(str, dict)
    mode_changed = Signal(str, str)
    modes_ready = Signal(list)

    ask_requested = Signal(str)
    mode_requested = Signal(str)
    preload_requested = Signal()
    shutdown_requested = Signal()

    def __init__(self, config_path: str | None = None, parent=None, autostart: bool = True):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = AgentWorker(config_path)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.start)
        self.ask_requested.connect(self._worker.ask)
        self.mode_requested.connect(self._worker.set_mode)
        self.preload_requested.connect(self._worker.preload)
        self.shutdown_requested.connect(self._worker.shutdown)

        self._worker.event.connect(self.event)
        self._worker.ready.connect(self.ready)
        self._worker.replied.connect(self.replied)
        self._worker.failed.connect(self.failed)
        self._worker.confirm_required.connect(self.confirm_required)
        self._worker.mode_changed.connect(self.mode_changed)
        self._worker.modes_ready.connect(self.modes_ready)

        if autostart:
            self.start()

    def start(self) -> None:
        """等界面把信号接好再启动线程，避免 early signal 丢失。"""
        if self._thread.isRunning():
            return
        self._thread.start()

    def ask(self, text: str) -> None:
        self.ask_requested.emit(text)

    def preload(self) -> None:
        self.preload_requested.emit()

    def set_mode(self, mode: str) -> None:
        self.mode_requested.emit(mode)

    def answer_confirm(self, allowed: bool) -> None:
        # 直接调用，不经信号——worker 正阻塞着等这个结果
        self._worker.resolve_confirm(bool(allowed))

    def shutdown(self) -> None:
        if not self._thread.isRunning():
            return
        # 先解锁可能挂起的工具确认，否则 worker 永远退不出来
        self._worker.resolve_confirm(False)
        self.shutdown_requested.emit()
        self._thread.quit()
        if not self._thread.wait(8000):
            self._thread.terminate()
            self._thread.wait(1000)
