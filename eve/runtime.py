"""运行时：把"一个模型"变成"一个能用的 Provider"。

这是 v0.4 的核心。三层分工：
- ModelConfig  —— 声明有什么（本地 GGUF 路径 / 云端 base_url）
- LocalEngine  —— 本地模型的生命周期（起进程、探活、停止、占端口）
- RuntimeManager —— 对外只暴露 provider_for(model_id)，本地云端走同一入口

UI 和 CLI 都只跟 RuntimeManager 打交道，永远不碰 subprocess。
"""

from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig, ConfigError, ModelConfig, ProviderConfig
from .provider import OpenAICompatProvider, Provider
from .secrets import KeyStore

HEALTH_TIMEOUT = 180.0
STOP_GRACE = 12.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class EngineState:
    model_id: str = ""
    state: str = "stopped"  # stopped | starting | ready | failed
    base_url: str = ""
    port: int = 0
    pid: int | None = None
    load_seconds: float = 0.0
    error: str = ""
    log_path: str = ""


class LocalEngine:
    """一个 llama.cpp 服务进程。"""

    def __init__(self, cfg: AppConfig, model: ModelConfig):
        self.cfg = cfg
        self.model = model
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.state = EngineState(model_id=model.id, state="stopped")
        self._lock = threading.Lock()

    # ---------- 启动 ----------

    def build_command(self, port: int) -> list[str]:
        engine = self.cfg.engine(self.model.engine)
        model_path = self.cfg.abs(self.model.path)
        cmd = [
            engine.bin,
            "-m", str(model_path),
            "-c", str(self.model.ctx),
            "-ngl", str(self.model.ngl),
            "-t", str(engine.threads),
            "--host", "127.0.0.1",
            "--port", str(port),
        ]
        if self.model.mmproj:
            cmd += ["--mmproj", str(self.cfg.abs(self.model.mmproj))]
        cmd += list(engine.extra_args)
        return cmd

    def _validate(self) -> None:
        engine = self.cfg.engine(self.model.engine)
        if not Path(engine.bin).exists():
            raise ConfigError(
                f"推理引擎不存在：{engine.bin}\n"
                f"请检查 config.toml 里 [engines.{self.model.engine}].bin"
            )
        model_path = self.cfg.abs(self.model.path)
        if not model_path.exists():
            raise ConfigError(f"模型文件不存在：{model_path}")
        if self.model.mmproj and not self.cfg.abs(self.model.mmproj).exists():
            raise ConfigError(f"视觉投影文件不存在：{self.cfg.abs(self.model.mmproj)}")

    def _healthy(self, port: int, timeout: float = 1.5) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=timeout
            ) as resp:
                return json.loads(resp.read().decode("utf-8")).get("status") == "ok"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return False

    def start(self) -> str:
        """启动并等待就绪，返回 base_url。"""
        with self._lock:
            self._validate()
            self.port = free_port()
            cmd = self.build_command(self.port)

            self.cfg.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.cfg.log_dir / f"engine-{self.model.id}.log"
            self.state = EngineState(
                model_id=self.model.id,
                state="starting",
                port=self.port,
                log_path=str(log_path),
            )

            started = time.perf_counter()
            log_file = log_path.open("w", encoding="utf-8", errors="replace")
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            except OSError as exc:
                log_file.close()
                self.state.state = "failed"
                self.state.error = f"启动失败：{exc}"
                raise ConfigError(self.state.error) from exc

            self.state.pid = self.proc.pid
            deadline = time.time() + HEALTH_TIMEOUT
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    self.state.state = "failed"
                    tail = _tail(log_path, 800)
                    self.state.error = f"引擎进程提前退出（code={self.proc.returncode}）"
                    raise ConfigError(f"{self.state.error}\n{tail}")
                if self._healthy(self.port):
                    self.state.state = "ready"
                    self.state.load_seconds = round(time.perf_counter() - started, 1)
                    self.state.base_url = f"http://127.0.0.1:{self.port}/v1"
                    return self.state.base_url
                time.sleep(0.35)

            self.stop()
            self.state.state = "failed"
            self.state.error = f"等待就绪超时（{HEALTH_TIMEOUT:.0f}s）"
            raise ConfigError(self.state.error)

    # ---------- 停止 ----------

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None or proc.poll() is not None:
            self.state.state = "stopped"
            return
        proc.terminate()
        try:
            proc.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self.state.state = "stopped"

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def _tail(path: Path, limit: int = 600) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


@dataclass
class RuntimeStatus:
    model_id: str = ""
    kind: str = ""
    state: str = "stopped"
    base_url: str = ""
    port: int = 0
    pid: int | None = None
    load_seconds: float = 0.0
    error: str = ""
    log_path: str = ""
    extra: dict = field(default_factory=dict)


class RuntimeManager:
    """对外唯一入口：给我 model_id，还你一个能 chat 的 Provider。"""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.keys = KeyStore(cfg.data_dir)
        self._provider: Provider | None = None
        self._model_id: str = ""
        self._engine: LocalEngine | None = None
        self._error: str = ""
        atexit.register(self.shutdown)

    # ---------- 核心 ----------

    def provider_for(self, model_id: str) -> Provider:
        if self._model_id == model_id and self._provider is not None:
            return self._provider

        self.shutdown()
        model = self.cfg.model(model_id)
        kind = model.detect_kind()

        if kind == "local_gguf":
            pcfg = self._start_local(model)
        elif kind == "cloud_api":
            pcfg = self._connect_cloud(model)
        else:
            raise ConfigError(f"模型 '{model_id}' 的 kind 不认识：{kind}")

        self._provider = OpenAICompatProvider(pcfg)
        self._model_id = model_id
        self._error = ""
        return self._provider

    def _start_local(self, model: ModelConfig) -> ProviderConfig:
        self._engine = LocalEngine(self.cfg, model)
        base_url = self._engine.start()
        return ProviderConfig(
            name=model.id,
            base_url=base_url,
            model=model.id,
            api_key="local-no-auth",
        )

    def _connect_cloud(self, model: ModelConfig) -> ProviderConfig:
        if not model.base_url:
            raise ConfigError(f"模型 '{model.id}' 是云端类型，但没配 base_url")

        api_key = ""
        if model.api_key_env:
            api_key = os.environ.get(model.api_key_env, "").strip()
        if not api_key:
            api_key = self.keys.get(model.id) or ""
        if not api_key:
            hint = f"，或设置环境变量 {model.api_key_env}" if model.api_key_env else ""
            raise ConfigError(
                f"模型 '{model.id}' 缺少 API Key。用 /key {model.id} 保存（DPAPI 加密）{hint}"
            )

        return ProviderConfig(
            name=model.id,
            base_url=model.base_url,
            model=model.model or model.id,
            api_key=api_key,
        )

    # ---------- 生命周期 ----------

    def shutdown(self) -> None:
        if self._engine is not None:
            try:
                self._engine.stop()
            finally:
                self._engine = None
        if self._provider is not None:
            self._provider = None
        self._model_id = ""

    def status(self) -> RuntimeStatus:
        if self._engine is not None:
            st = self._engine.state
            model = self.cfg.model(st.model_id) if st.model_id else None
            return RuntimeStatus(
                model_id=st.model_id,
                kind=(model.detect_kind() if model else "local_gguf"),
                state=st.state,
                base_url=st.base_url,
                port=st.port,
                pid=st.pid,
                load_seconds=st.load_seconds,
                error=st.error,
                log_path=st.log_path,
            )
        if self._model_id:
            model = self.cfg.models.get(self._model_id)
            return RuntimeStatus(
                model_id=self._model_id,
                kind=(model.detect_kind() if model else "cloud_api"),
                state="ready",
                base_url=(model.base_url if model else ""),
            )
        return RuntimeStatus()

    @property
    def active_model_id(self) -> str:
        return self._model_id
