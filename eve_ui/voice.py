"""把语音输入接到 Qt 上。

录音和识别都会阻塞（录音等静音，识别要跑模型），所以必须放在子线程；
界面只接收信号。

流程：点按钮 → listening → 录到静音 → transcribing → 文本 → 主窗口发出去。
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QThread, Signal, Slot

from eve_core.voice import (
    SAMPLE_RATE,
    AudioConfig,
    KwsDetector,
    Recorder,
    SherpaRecognizer,
    VoiceSettings,
)


class VoiceWorker(QObject):
    state = Signal(str)      # listening | transcribing | idle
    transcript = Signal(str)
    failed = Signal(str)

    def __init__(self, settings: VoiceSettings):
        super().__init__()
        self._settings = settings
        self._recorder = None
        self._recognizer = None

    def _ensure(self) -> None:
        if self._recorder is None:
            cfg = AudioConfig(
                silence_ms=self._settings.silence_ms,
                max_seconds=self._settings.max_seconds,
            )
            self._recorder = Recorder(cfg)
        if self._recognizer is None:
            self._recognizer = SherpaRecognizer(self._settings.asr_model)

    @Slot()
    def listen(self) -> None:
        try:
            self._ensure()
            if not self._recorder.available:
                self.failed.emit("没有可用的麦克风（sounddevice 未安装或设备被占用）")
                return
            if not self._recognizer.available:
                self.failed.emit(f"语音识别不可用：{self._recognizer.error}")
                return

            self.state.emit("listening")
            samples = self._recorder.record_utterance()
            if samples is None or len(samples) == 0:
                self.state.emit("idle")
                self.failed.emit("没听到说话")
                return

            self.state.emit("transcribing")
            text = self._recognizer.transcribe(samples)
            self.state.emit("idle")

            if text:
                self.transcript.emit(text)
            else:
                self.failed.emit("没听清，再说一次？")
        except Exception as exc:  # 麦克风/模型出错不能炸掉界面
            self.state.emit("idle")
            self.failed.emit(f"语音出错：{type(exc).__name__}: {exc}")


class VoiceBridge(QObject):
    """主线程侧的门面。"""

    state_changed = Signal(str)
    transcript = Signal(str)
    failed = Signal(str)

    listen_requested = Signal()

    def __init__(self, settings: VoiceSettings, parent=None):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = VoiceWorker(settings)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(lambda: None)
        self.listen_requested.connect(self._worker.listen)
        self._worker.state.connect(self.state_changed)
        self._worker.transcript.connect(self.transcript)
        self._worker.failed.connect(self.failed)
        self._thread.start()

    def listen(self) -> None:
        self.listen_requested.emit()

    def shutdown(self) -> None:
        if not self._thread.isRunning():
            return
        self._thread.quit()
        if not self._thread.wait(3000):
            self._thread.terminate()
            self._thread.wait(1000)


class WakeWorker(QObject):
    """常听唤醒：麦克风一直开，听到「伊芙」就发出一声。

    注意这个模型是流式的：必须按 ~100ms 分块喂，整段一次性喂进去检测不到。
    """

    woke = Signal(str)
    failed = Signal(str)

    CHUNK = 1600  # 100ms @16k

    def __init__(self, settings: VoiceSettings):
        super().__init__()
        self._settings = settings
        self._enabled = threading.Event()
        self._stop = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            self.failed.emit(f"没有麦克风：{exc}")
            return

        tokens, label = self._settings.wake_tokens()
        if not tokens:
            self.failed.emit(f"唤醒词「{label}」还没有拼音 token")
            return

        detector = KwsDetector(self._settings.kws_model, [(tokens, label)])
        if not detector.available:
            self.failed.emit(f"唤醒不可用：{detector.error}")
            return

        self._enabled.set()
        try:
            while not self._stop.is_set():
                if not self._enabled.is_set():
                    time.sleep(0.05)
                    continue

                # 每次进听都新开一路流：暂停时关掉，免得和按键录音抢设备
                with sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=self.CHUNK,
                ) as stream:
                    detector.reset()
                    while self._enabled.is_set() and not self._stop.is_set():
                        data, _ = stream.read(self.CHUNK)
                        samples = data[:, 0] if getattr(data, "ndim", 1) > 1 else data
                        hit = detector.accept(samples)
                        if hit:
                            self._enabled.clear()
                            self.woke.emit(hit)
                            break
        except Exception as exc:
            self.failed.emit(f"唤醒监听出错：{type(exc).__name__}: {exc}")

    def set_enabled(self, value: bool) -> None:
        if value:
            self._enabled.set()
        else:
            self._enabled.clear()

    def stop(self) -> None:
        self._stop.set()
        self._enabled.clear()


class WakeBridge(QObject):
    woke = Signal(str)
    failed = Signal(str)

    def __init__(self, settings: VoiceSettings, parent=None):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = WakeWorker(settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.woke.connect(self.woke)
        self._worker.failed.connect(self.failed)
        self._thread.start()

    def enable(self) -> None:
        self._worker.set_enabled(True)

    def disable(self) -> None:
        self._worker.set_enabled(False)

    def shutdown(self) -> None:
        self._worker.stop()
        if not self._thread.isRunning():
            return
        self._thread.quit()
        if not self._thread.wait(4000):
            self._thread.terminate()
            self._thread.wait(1000)
