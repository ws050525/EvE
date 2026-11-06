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

CHIP_QSS = f"""
QPushButton {{
    background: transparent;
    border: 1px solid {LINE};
    border-radius: 15px;
    color: {TEXT1};
    font-family: {FONT_STACK};
    font-size: 12px;
    padding: 8px 16px;
}}
QPushButton:hover {{
    border-color: #38424E;
    background: {PANEL};
    color: {TEXT0};
}}
QPushButton:pressed {{
    background: {BUBBLE};
}}
"""

MODE_CHIP_QSS = f"""QPushButton {{
    color: {TEXT1};
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 10px;
    padding: 0 10px;
    font-family: {FONT_STACK};
    font-size: 11px;
}}
QPushButton:hover {{
    color: {TEXT0};
    border-color: #3A4350;
}}
QPushButton::menu-indicator {{ width: 0; }}
"""

# 许可交互的两个按钮：允许（实心强调）与拒绝（描边低调）。
# 悬浮球气泡和主窗口卡片共用这一套，避免两处长得不一样。
ALLOW_BUTTON_QSS = f"""
QPushButton {{
    background: {ACCENT};
    border: none;
    border-radius: 8px;
    color: {BG};
    font-family: {FONT_STACK};
    font-size: 11px;
    font-weight: 600;
    padding: 5px 14px;
}}
QPushButton:hover {{ background: #74EBD4; }}
"""

DENY_BUTTON_QSS = f"""
QPushButton {{
    background: transparent;
    border: 1px solid {LINE};
    border-radius: 8px;
    color: {TEXT1};
    font-family: {FONT_STACK};
    font-size: 11px;
    padding: 5px 12px;
}}
QPushButton:hover {{
    color: {TEXT0};
    border-color: #3A4350;
    background: {PANEL};
}}
"""

# 主窗口里的许可卡片
CONFIRM_CARD_QSS = f"""
QFrame#ConfirmCard {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 12px;
}}
"""

# 输入框旁边的语音按钮
VOICE_BUTTON_QSS = f"""
QPushButton {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 10px;
    color: {TEXT1};
    font-family: {FONT_STACK};
    font-size: 12px;
    padding: 0 14px;
}}
QPushButton:hover {{
    color: {TEXT0};
    border-color: #3A4350;
}}
QPushButton:disabled {{
    color: {TEXT2};
}}
"""

# API Key 设置对话框
DIALOG_QSS = f"""
QDialog {{
    background: {PANEL};
    color: {TEXT0};
    font-family: {FONT_STACK};
}}
QLabel {{ color: {TEXT1}; font-size: 11px; }}
QComboBox {{
    background: {BUBBLE};
    border: 1px solid {LINE};
    border-radius: 8px;
    color: {TEXT0};
    padding: 6px 10px;
    font-family: {FONT_STACK};
    font-size: 12px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    border: 1px solid {LINE};
    color: {TEXT0};
    selection-background-color: {BUBBLE};
}}
"""

MENU_QSS = f"""
QMenu {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 10px;
    padding: 6px;
    color: {TEXT0};
    font-family: {FONT_STACK};
    font-size: 12px;
}}
QMenu::item {{
    padding: 7px 22px 7px 14px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {BUBBLE};
}}
"""

BROWSER_QSS = f"""QTextBrowser {{    background: transparent;
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
