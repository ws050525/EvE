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
from eve_core.pricing import price_for


def model_rows(cfg, active_model_id: str, keys=None) -> list[dict]:
    """给界面用的模型清单：本地排前面，带单价，标出当前那个和有没有 Key。"""
    rows: list[dict] = []
    for model_id, model in cfg.models.items():
        price = price_for(model)
        kind = model.detect_kind()
        key_ref = model.api_key_ref or model.id
        has_key = None
        if kind == "cloud_api" and keys is not None:
            has_key = bool(keys.get(key_ref))
        rows.append(
            {
                "id": model_id,
                "label": model.label,
                "kind": kind,
                "price_in": price.input,
                "price_out": price.output,
                "free": price.free,
                "active": model_id == active_model_id,
                "key_ref": key_ref,
                "has_key": has_key,
            }
        )
    rows.sort(key=lambda row: (row["kind"] != "local_gguf", row["id"]))
    return rows


def key_rows(cfg, keys=None) -> list[dict]:
    """按 key id 归并（flash / pro 共用 deepseek 这把），给设置对话框用。"""
    grouped: dict[str, list[str]] = {}
    for model in cfg.models.values():
        if model.detect_kind() != "cloud_api":
            continue
        ref = model.api_key_ref or model.id
        grouped.setdefault(ref, []).append(model.label)

    rows = []
    for ref, labels in grouped.items():
        rows.append(
            {
                "id": ref,
                "label": f"{ref} · {' / '.join(labels)}",
                "has_key": bool(keys.get(ref)) if keys is not None else None,
            }
        )
    rows.sort(key=lambda row: row["id"])
    return rows


class AgentWorker(QObject):
    event = Signal(str, dict)      # kind, data
    ready = Signal(str, str)       # model_id, label
    replied = Signal(str)
    failed = Signal(str)
    confirm_required = Signal(str, dict)   # 工具名、参数
    mode_changed = Signal(str, str)        # mode, model_id
    modes_ready = Signal(list)
    models_ready = Signal(list)            # [{id,label,kind,price_in,price_out,active}]
    keys_ready = Signal(list)              # [{id,label,has_key}]
    limits_changed = Signal(int, int, int)  # 预算设定（0=自动）、生效预算、步数上限
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
            self.modes_ready.emit(
                [
                    {"id": name, "label": mode.display}
                    for name, mode in sorted(cfg.modes.items())
                ]
            )
            self._emit_keys()
            self._emit_limits()
            self.ready.emit(self._agent.model_id, cfg.model(self._agent.model_id).label)
        except Exception as exc:  # 配置错误也要让界面显示出来
            self.failed.emit(str(exc))

    def _emit_keys(self) -> None:
        if self._agent is None:
            return
        self.keys_ready.emit(key_rows(self._agent.cfg, self._agent.runtime.keys))
        self.models_ready.emit(
            model_rows(
                self._agent.cfg, self._agent.model_id, self._agent.runtime.keys
            )
        )

    def _emit_limits(self) -> None:
        if self._agent is not None:
            self.limits_changed.emit(
                self._agent.context_setting,
                self._agent.context_limit,
                self._agent.max_steps,
            )

    def _on_event(self, ev) -> None:
        # 这里在子线程执行，emit 会自动排队到主线程
        self.event.emit(ev.kind, ev.data)

    @Slot(bool)
    def set_auto_confirm(self, value: bool) -> None:
        """最高权限开关：打开后所有工具（含高风险）都不再询问。"""
        if self._agent is None:
            return
        self._agent.cfg.auto_confirm = bool(value)
        note = (
            "已打开最高权限：读写文件、跑命令都不再询问"
            if value
            else "已关闭最高权限：高风险工具恢复询问"
        )
        self.event.emit("settings", {"message": note})

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

    @Slot()
    def cancel(self) -> None:
        """停止当前任务：agent 会在这一步结束后退出循环。

        直接改标志位，不走信号排队——worker 此刻正阻塞在模型请求里，排队等于没停。
        """
        if self._agent is not None:
            self._agent.cancel()
        self._decision.set()  # 顺手解开可能挂着的工具确认

    @Slot()
    def resume(self) -> None:
        """接着上次没做完的活：把上下文和计划捞回来，再补一句"从哪继续"。"""
        if self._agent is None:
            self.failed.emit("agent 尚未就绪")
            return
        try:
            brief = self._agent.resume()
        except Exception as exc:
            self.failed.emit(f"续跑失败：{exc}")
            return
        if not brief:
            self.failed.emit("没有可以继续的任务")
            return
        try:
            self.replied.emit(self._agent.send(brief, confirm=self._confirm))
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot(str)
    def set_mode(self, mode_name: str) -> None:
        if self._agent is None:
            return
        try:
            self._agent.switch_mode(mode_name)
            self.mode_changed.emit(self._agent.mode_name, self._agent.model_id)
            self._emit_keys()
            self._emit_limits()
            self.ready.emit(
                self._agent.model_id, self._agent.cfg.model(self._agent.model_id).label
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot(str)
    def set_model(self, model_id: str) -> None:
        """换模型但保留档位（云端测试用）。"""
        if self._agent is None:
            return
        try:
            self._agent.switch_model(model_id)
            self._emit_keys()
            self._emit_limits()
            self.ready.emit(
                self._agent.model_id, self._agent.cfg.model(self._agent.model_id).label
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot(int)
    def set_context_limit(self, tokens: int) -> None:
        if self._agent is None:
            return
        try:
            note = self._agent.set_context_limit(tokens)
            self.event.emit("settings", {"message": note})
            self._emit_limits()
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot(int)
    def set_max_steps(self, steps: int) -> None:
        if self._agent is None:
            return
        try:
            note = self._agent.set_max_steps(steps)
            self.event.emit("settings", {"message": note})
            self._emit_limits()
        except Exception as exc:
            self.failed.emit(str(exc))

    @Slot(str, str)
    def set_key(self, key_id: str, secret: str) -> None:
        """存 API Key：DPAPI 加密后落到 .data/keys.json，明文只在内存里过一下。"""
        if self._agent is None:
            return
        try:
            self._agent.runtime.keys.set(key_id, secret)
            # 之前因为没 key 而失败过的 provider 要重建，否则改了也不生效
            self._agent.forget_provider()
            self._emit_keys()
            self.event.emit(
                "settings", {"message": f"{key_id} 的 API Key 已加密保存"}
            )
        except Exception as exc:
            self.failed.emit(f"保存 Key 失败：{exc}")

    @Slot(str)
    def delete_key(self, key_id: str) -> None:
        if self._agent is None:
            return
        try:
            removed = self._agent.runtime.keys.delete(key_id)
            self._agent.forget_provider()
            self._emit_keys()
            self.event.emit(
                "settings",
                {"message": f"已删除 {key_id} 的 Key" if removed else f"{key_id} 本来就没有 Key"},
            )
        except Exception as exc:
            self.failed.emit(f"删除 Key 失败：{exc}")

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
    models_ready = Signal(list)
    keys_ready = Signal(list)
    limits_changed = Signal(int, int, int)

    ask_requested = Signal(str)
    mode_requested = Signal(str)
    model_requested = Signal(str)
    context_requested = Signal(int)
    steps_requested = Signal(int)
    key_set_requested = Signal(str, str)
    key_delete_requested = Signal(str)
    auto_confirm_requested = Signal(bool)
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
        self.model_requested.connect(self._worker.set_model)
        self.context_requested.connect(self._worker.set_context_limit)
        self.steps_requested.connect(self._worker.set_max_steps)
        self.key_set_requested.connect(self._worker.set_key)
        self.key_delete_requested.connect(self._worker.delete_key)
        self.auto_confirm_requested.connect(self._worker.set_auto_confirm)
        self.preload_requested.connect(self._worker.preload)
        self.shutdown_requested.connect(self._worker.shutdown)

        self._worker.event.connect(self.event)
        self._worker.ready.connect(self.ready)
        self._worker.replied.connect(self.replied)
        self._worker.failed.connect(self.failed)
        self._worker.confirm_required.connect(self.confirm_required)
        self._worker.mode_changed.connect(self.mode_changed)
        self._worker.modes_ready.connect(self.modes_ready)
        self._worker.models_ready.connect(self.models_ready)
        self._worker.keys_ready.connect(self.keys_ready)
        self._worker.limits_changed.connect(self.limits_changed)

        if autostart:
            self.start()

    def start(self) -> None:
        """等界面把信号接好再启动线程，避免 early signal 丢失。"""
        if self._thread.isRunning():
            return
        self._thread.start()

    def cancel(self) -> None:
        # 直接调用：worker 正阻塞在模型请求里，排队的信号要等它跑完才会被处理
        self._worker.cancel()

    def resume(self) -> None:
        # 同理：续跑前 worker 是空闲的，可以安全排队；但直接调用更省事也不会错
        self._worker.resume()

    def ask(self, text: str) -> None:
        self.ask_requested.emit(text)

    def preload(self) -> None:
        self.preload_requested.emit()

    def set_mode(self, mode: str) -> None:
        self.mode_requested.emit(mode)

    def set_model(self, model_id: str) -> None:
        self.model_requested.emit(model_id)

    def set_context_limit(self, tokens: int) -> None:
        self.context_requested.emit(int(tokens))

    def set_max_steps(self, steps: int) -> None:
        self.steps_requested.emit(int(steps))

    def set_key(self, key_id: str, secret: str) -> None:
        self.key_set_requested.emit(key_id, secret)

    def delete_key(self, key_id: str) -> None:
        self.key_delete_requested.emit(key_id)

    def set_auto_confirm(self, value: bool) -> None:
        self.auto_confirm_requested.emit(bool(value))

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
