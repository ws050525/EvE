"""GUI 启动入口。"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .main_window import ChatWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv or sys.argv)
    app.setApplicationName("EVE")
    app.setApplicationDisplayName("EVE")

    window = ChatWindow()
    window.show()
    app.aboutToQuit.connect(window.shutdown_agent)

    model = os.environ.get("EVE_MODEL")
    if model:
        delay = int(os.environ.get("EVE_MODEL_DELAY", "6000"))
        QTimer.singleShot(delay, lambda: window._bridge.set_model(model))

    mode = os.environ.get("EVE_MODE")
    if mode:
        delay = int(os.environ.get("EVE_MODE_DELAY", "1200"))
        QTimer.singleShot(delay, lambda: window._switch_mode(mode))

    demo = os.environ.get("EVE_DEMO")
    if demo:
        delay = int(os.environ.get("EVE_DEMO_DELAY", "2500"))
        QTimer.singleShot(delay, lambda: window.demo_send(demo))

    # 开发期：窗口自身截图后退出，用于把界面图放进文档
    shot = os.environ.get("EVE_SHOT")
    if shot:
        delay = int(os.environ.get("EVE_SHOT_DELAY", "6000"))

        def capture() -> None:
            window.grab().save(shot)
            app.quit()

        QTimer.singleShot(delay, capture)

    # 开发期：整屏截图（主窗收起成悬浮球时，只能截屏幕）
    shot_screen = os.environ.get("EVE_SHOT_SCREEN")
    if shot_screen:
        delay = int(os.environ.get("EVE_SHOT_DELAY", "6000"))

        def capture_screen() -> None:
            screen = app.primaryScreen()
            if screen is not None:
                screen.grabWindow(0).save(shot_screen)
            app.quit()

        QTimer.singleShot(delay, capture_screen)

    # 开发期：单独截悬浮球（含气泡）
    shot_orb = os.environ.get("EVE_SHOT_ORB")
    if shot_orb:
        delay = int(os.environ.get("EVE_SHOT_DELAY", "6000"))

        def capture_orb() -> None:
            orb = getattr(window, "_orb", None)
            if orb is not None and orb.isVisible():
                orb.grab().save(shot_orb)
            app.quit()

        QTimer.singleShot(delay, capture_orb)

    return app.exec()
