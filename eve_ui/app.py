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

    demo = os.environ.get("EVE_DEMO")
    if demo:
        QTimer.singleShot(2500, lambda: window.demo_send(demo))

    # 开发期：窗口自身截图后退出，用于把界面图放进文档
    shot = os.environ.get("EVE_SHOT")
    if shot:
        delay = int(os.environ.get("EVE_SHOT_DELAY", "6000"))

        def capture() -> None:
            window.grab().save(shot)
            app.quit()

        QTimer.singleShot(delay, capture)

    return app.exec()
