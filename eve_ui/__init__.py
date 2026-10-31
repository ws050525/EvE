"""EVE 的界面层（v0.5 开始填充）。

规划中的模块：
- main_window.py   主窗口：空态莫比乌斯 / 消息列表 / 输入框
- mobius.py        莫比乌斯的参数化绘制与状态动画
- orb.py           悬浮球：玻璃球 + 光点眼 + 表情参数
- bubbles.py       气泡：进度汇报，最多 3 条
- model_panel.py   模型面板：选 GGUF / 切云端 / 存 key
- events.py        agent 事件 → Qt Signal 的桥

现在留空，是因为**事件通道要先于界面实现**（见 docs/design-ui.md）。
"""
