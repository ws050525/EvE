"""把语音播报接到 Qt 上：合成和播放都会阻塞，放子线程。

播报期间主窗口会暂停唤醒监听，避免把自己的声音当成「伊芙」（自激）。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot

from eve_core.tts import PiperTts, SapiTts, TtsSettings


class TtsWorker(QObject):
    started = Signal()
    finished = Signal(bool)
    failed = Signal(str)

    speak_requested = Signal(str)

    def __init__(self, settings: TtsSettings):
        super().__init__()
        self._settings = settings
        self._backend = None
        self._fallback = None

    def _pick_backend(self):
        if self._backend is not None:
            return self._backend

        piper = PiperTts(
            self._settings.model,
            speed=self._settings.speed,
            speaker=self._settings.speaker,
            volume=self._settings.volume,
            noise_scale=self._settings.noise_scale,
            noise_scale_w=self._settings.noise_scale_w,
        )
        if piper.available:
            self._backend = piper
            return self._backend

        # 模型不在就用系统音色兜底，至少能出声
        sapi = SapiTts(speed=self._settings.speed)
        if sapi.available:
            self._backend = sapi
            self._fallback = piper.error
            return self._backend

        raise RuntimeError(piper.error or "没有可用的语音合成后端")

    @Slot(str)
    def speak(self, text: str) -> None:
        try:
            backend = self._pick_backend()
            self.started.emit()
            ok = backend.speak(text)
            self.finished.emit(bool(ok))
        except Exception as exc:
            self.finished.emit(False)
            self.failed.emit(f"播报失败：{type(exc).__name__}: {exc}")


class TtsBridge(QObject):
    started = Signal()
    finished = Signal(bool)
    failed = Signal(str)

    speak_requested = Signal(str)

    def __init__(self, settings: TtsSettings, parent=None):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = TtsWorker(settings)
        self._worker.moveToThread(self._thread)
        self.speak_requested.connect(self._worker.speak)
        self._worker.started.connect(self.started)
        self._worker.finished.connect(self.finished)
        self._worker.failed.connect(self.failed)
        self._thread.start()

    def speak(self, text: str) -> None:
        if text.strip():
            self.speak_requested.emit(text)

    def shutdown(self) -> None:
        if not self._thread.isRunning():
            return
        self._thread.quit()
        if not self._thread.wait(3000):
            self._thread.terminate()
            self._thread.wait(1000)
