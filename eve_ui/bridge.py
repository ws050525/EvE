"""把 agent 接到 Qt 上。

关键约束：Agent 会阻塞（加载模型、等模型吐字），所以它必须活在子线程里；
所有界面绘制只在主线程。事件通过 Signal 跨线程，Qt 自动排队。

这个文件是 UI 与业务逻辑之间唯一的桥，界面代码不 import eve_agent。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot

from eve_agent import Agent
from eve_core.config import load_config
from eve_core.events import EventBus


class AgentWorker(QObject):
    event = Signal(str, dict)      # kind, data
    ready = Signal(str, str)       # model_id, label
    replied = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, config_path: str | None = None):
        super().__init__()
        self._config_path = config_path
        self._agent: Agent | None = None

    @Slot()
    def start(self) -> None:
        try:
            cfg = load_config(self._config_path)
            bus = EventBus()
            bus.subscribe(self._on_event)
            self._agent = Agent(cfg, cfg.default_mode, bus=bus)
            self.ready.emit(self._agent.model_id, cfg.model(self._agent.model_id).label)
        except Exception as exc:  # 配置错误也要让界面显示出来
            self.failed.emit(str(exc))

    def _on_event(self, ev) -> None:
        # 这里在子线程执行，emit 会自动排队到主线程
        self.event.emit(ev.kind, ev.data)

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
            self.replied.emit(self._agent.send(text))
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot(str)
    def set_mode(self, mode_name: str) -> None:
        if self._agent is None:
            return
        try:
            self._agent.switch_mode(mode_name)
            self.ready.emit(self._agent.model_id, self._agent.mode.model)
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

    ask_requested = Signal(str)
    mode_requested = Signal(str)
    preload_requested = Signal()
    shutdown_requested = Signal()

    def __init__(self, config_path: str | None = None, parent=None):
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

        self._thread.start()

    def ask(self, text: str) -> None:
        self.ask_requested.emit(text)

    def preload(self) -> None:
        self.preload_requested.emit()

    def set_mode(self, mode: str) -> None:
        self.mode_requested.emit(mode)

    def shutdown(self) -> None:
        if not self._thread.isRunning():
            return
        self.shutdown_requested.emit()
        self._thread.quit()
        self._thread.wait(8000)
