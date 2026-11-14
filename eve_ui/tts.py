"""把语音播报接到 Qt 上：合成和播放都会阻塞，放子线程。

两种播法：

- `speak(text)`：一整段话，阻塞到播完。用于唤醒应答这种短句。
- `stream_begin()` / `stream_text(delta)` / `stream_end()`：边生成边念。
  模型吐一句就合成一句、播一句，声音不会落后于界面上的文字。
  这就是"播报跟不上文本生成"的修法——以前一定要等整段回复出完才开始念。

播报期间主窗口会暂停唤醒监听，避免把自己的声音当成「伊芙」（自激）。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot

from eve_core.tts import PiperTts, SapiTts, TtsSettings, VoiceFx


class TtsWorker(QObject):
    started = Signal()
    finished = Signal(bool)
    failed = Signal(str)

    speak_requested = Signal(str)
    stream_begin_requested = Signal()
    stream_text_requested = Signal(str)
    stream_end_requested = Signal()
    cancel_requested = Signal()
    preview_requested = Signal(str)
    model_requested = Signal(str)

    def __init__(self, settings: TtsSettings):
        super().__init__()
        self._settings = settings
        self._backend = None
        self._session = None
        self._session_mark = 0  # 只让「当前这次」播报的结束信号生效
        self._sent_text = ""  # 兜底后端（SAPI）没有流式会话时，攒起来一次念

    def _build(self, model_path: str) -> PiperTts:
        return PiperTts(
            model_path,
            speed=self._settings.speed,
            speaker=self._settings.speaker,
            volume=self._settings.volume,
            noise_scale=self._settings.noise_scale,
            noise_scale_w=self._settings.noise_scale_w,
            fx=VoiceFx(
                enabled=self._settings.fx,
                warmth=self._settings.warmth,
                clarity=self._settings.clarity,
                deharsh=self._settings.deharsh,
                space=self._settings.space,
                compression=self._settings.compression,
            ),
        )

    def _pick_backend(self):
        if self._backend is not None:
            return self._backend

        piper = self._build(self._settings.model)
        if piper.available:
            self._backend = piper
            return self._backend

        # 模型不在就用系统音色兜底，至少能出声
        sapi = SapiTts(speed=self._settings.speed)
        if sapi.available:
            self._backend = sapi
            return self._backend

        raise RuntimeError(piper.error or "没有可用的语音合成后端")

    # ---------- 会话 ----------

    def _end_session(self) -> None:
        session = self._session
        self._session = None
        self._session_mark = 0
        if session is not None:
            try:
                session.cancel()
            except Exception:
                pass

    def _new_session(self):
        self._end_session()
        try:
            backend = self._pick_backend()
        except Exception as exc:
            self.finished.emit(False)
            self.failed.emit(f"播报失败：{type(exc).__name__}: {exc}")
            return None

        if not hasattr(backend, "new_session"):
            return None  # SAPI 走 speak() 兜底

        self._session_mark += 1
        mark = self._session_mark
        session = backend.new_session(
            on_start=lambda: self.started.emit(),
            on_done=lambda ok: self._on_session_done(mark, ok),
            on_error=lambda message: self.failed.emit(message),
        )
        self._session = session  # 先登记再开线程：播得快也不会漏掉自己的结束信号
        session.start()
        return session

    def _on_session_done(self, mark: int, ok: bool) -> None:
        # 在播放线程里回调，直接 emit 信号是安全的（Qt 会排队到主线程）
        if mark != self._session_mark:
            return  # 已经被新的一次播报取代，别去动当前的唤醒状态
        self._session = None
        self._session_mark = 0
        self.finished.emit(bool(ok))

    # ---------- 播报 ----------

    @Slot()
    def preload(self) -> None:
        """提前把合成器拉起来。VITS 首次加载要 2 秒左右，不预热的话
        第一句回复要等到那时候才出声，听感就是"半天没反应"。"""
        try:
            backend = self._pick_backend()
        except Exception:
            return  # 预热失败不算错，真播的时候再报
        ensure = getattr(backend, "_ensure", None)
        if callable(ensure):
            try:
                ensure()
                backend.synthesize("预热")  # 第一次合成还会建一些内部缓冲
            except Exception:
                pass

    @Slot(str)
    def speak(self, text: str) -> None:
        session = self._new_session()
        if session is not None:
            session.push(text)
            session.close()
            return
        self._speak_blocking(text)

    def _speak_blocking(self, text: str) -> None:
        try:
            backend = self._pick_backend()
            self.started.emit()
            ok = backend.speak(text)
            self.finished.emit(bool(ok))
        except Exception as exc:
            self.finished.emit(False)
            self.failed.emit(f"播报失败：{type(exc).__name__}: {exc}")

    @Slot()
    def stream_begin(self) -> None:
        self._sent_text = ""
        self._new_session()

    @Slot(str)
    def stream_text(self, delta: str) -> None:
        if not delta:
            return
        session = self._session
        if session is not None:
            session.push(delta)
            return
        self._sent_text += delta

    @Slot()
    def stream_end(self) -> None:
        session = self._session
        if session is not None:
            # 不在这里清掉引用：播完还要靠 _on_session_done 通知主窗口
            session.close()
            return
        buffered, self._sent_text = self._sent_text, ""
        if buffered.strip():
            self._speak_blocking(buffered)

    @Slot()
    def cancel(self) -> None:
        self._sent_text = ""
        self._end_session()

    @Slot(str)
    def preview(self, model_path: str) -> None:
        """试听某个音色，不动当前正在用的那个。"""
        try:
            engine = self._build(model_path)
            if not engine.available:
                self.failed.emit(f"这个音色用不了：{engine.error}")
                return
            self.started.emit()
            ok = engine.speak(PREVIEW_TEXT)
            self.finished.emit(bool(ok))
        except Exception as exc:
            self.finished.emit(False)
            self.failed.emit(f"试听失败：{type(exc).__name__}: {exc}")

    @Slot(str)
    def set_model(self, model_path: str) -> None:
        """切换音色：丢掉旧的合成器，下次说话用新模型。"""
        self._settings.model = model_path
        self._end_session()
        self._backend = None


PREVIEW_TEXT = "你好呀，我是伊芙。这个声音你听着还顺耳吗？"


class TtsBridge(QObject):
    started = Signal()
    finished = Signal(bool)
    failed = Signal(str)

    speak_requested = Signal(str)
    stream_begin_requested = Signal()
    stream_text_requested = Signal(str)
    stream_end_requested = Signal()
    cancel_requested = Signal()
    preview_requested = Signal(str)
    model_requested = Signal(str)
    preload_requested = Signal()

    def __init__(self, settings: TtsSettings, parent=None):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = TtsWorker(settings)
        self._worker.moveToThread(self._thread)
        self.speak_requested.connect(self._worker.speak)
        self.stream_begin_requested.connect(self._worker.stream_begin)
        self.stream_text_requested.connect(self._worker.stream_text)
        self.stream_end_requested.connect(self._worker.stream_end)
        self.cancel_requested.connect(self._worker.cancel)
        self.preview_requested.connect(self._worker.preview)
        self.model_requested.connect(self._worker.set_model)
        self.preload_requested.connect(self._worker.preload)
        self._worker.started.connect(self.started)
        self._worker.finished.connect(self.finished)
        self._worker.failed.connect(self.failed)
        self._thread.start()

    def speak(self, text: str) -> None:
        if text.strip():
            self.speak_requested.emit(text)

    def preload(self) -> None:
        self.preload_requested.emit()

    def stream_begin(self) -> None:
        self.stream_begin_requested.emit()

    def stream_text(self, delta: str) -> None:
        if delta:
            self.stream_text_requested.emit(delta)

    def stream_end(self) -> None:
        self.stream_end_requested.emit()

    def cancel(self) -> None:
        self.cancel_requested.emit()

    def preview(self, model_path: str) -> None:
        self.preview_requested.emit(model_path)

    def set_model(self, model_path: str) -> None:
        self.model_requested.emit(model_path)

    def shutdown(self) -> None:
        if not self._thread.isRunning():
            return
        self.cancel()
        self._thread.quit()
        if not self._thread.wait(3000):
            self._thread.terminate()
            self._thread.wait(1000)
