"""界面配色与通用样式。一处定义，全局引用。"""

from __future__ import annotations

BG = "#0B0D10"
PANEL = "#12151A"
BUBBLE = "#1A1E25"
LINE = "#252A33"
TEXT0 = "#E9ECF1"
TEXT1 = "#9BA4B2"
TEXT2 = "#5E6672"
ACCENT = "#5CE1C8"
ACCENT_RGB = (92, 225, 200)
VIOLET_RGB = (124, 140, 255)
WARN = "#F0B563"
DANGER = "#EF6B6B"

FONT_STACK = '"Microsoft YaHei UI", "Segoe UI", "Microsoft YaHei", sans-serif'

INPUT_QSS = f"""
QLineEdit {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 10px;
    padding: 10px 14px;
    color: {TEXT0};
    font-family: {FONT_STACK};
    font-size: 14px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}
QLineEdit:focus {{
    border: 1px solid #2F3944;
}}
QLineEdit:disabled {{
    color: {TEXT2};
}}
"""

FLAT_BUTTON_QSS = f"""
QPushButton {{
    background: transparent;
    border: none;
    color: {TEXT1};
    font-family: {FONT_STACK};
    font-size: 13px;
    padding: 6px 10px;
    border-radius: 8px;
}}
QPushButton:hover {{
    background: {BUBBLE};
    color: {TEXT0};
}}
QPushButton:disabled {{
    color: {TEXT2};
}}
"""

SEND_BUTTON_QSS = f"""
QPushButton {{
    background: {ACCENT};
    border: none;
    border-radius: 10px;
    color: {BG};
    font-family: {FONT_STACK};
    font-size: 13px;
    font-weight: 600;
    padding: 10px 18px;
}}
QPushButton:hover {{
    background: #74EBD4;
}}
QPushButton:disabled {{
    background: #23302F;
    color: {TEXT2};
}}
"""

BROWSER_QSS = f"""
QTextBrowser {{
    background: transparent;
    border: none;
    color: {TEXT0};
    font-family: {FONT_STACK};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: #2A313B;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #3A4350;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""
