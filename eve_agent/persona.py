"""伊芙的人格状态机。

提示词只管"语气"，这里管"行为"：
- 被当工具时的分级抗议
- 口头禅的频率
- 颜文字的选用与节流
- 什么时候该禁掉工具

全部是确定性规则，不依赖模型判断——因为 4B 模型判断"用户是不是在支使我"并不可靠。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 已在本机 Microsoft YaHei 下验过字形的一张白名单
KAOMOJI = {
    "calm": "(・ω・)",
    "happy": "(｡･ω･｡)",
    "determined": "(｀・ω・´)",
    "smug": "(￣ー￣)",
    "sorry": "(´・ω・`)",
    "awkward": "(・_・;)",
    "surprised": "(°ロ°)",
}
WHITELIST = tuple(KAOMOJI.values())

# 命令式请求的起始词（中文没有词边界，只能按前缀判）
_VERB_PREFIXES = (
    "打开",
    "启动",
    "关掉",
    "关闭",
    "搜索",
    "搜一下",
    "搜下",
    "查一下",
    "查查",
    "帮我打开",
    "给我打开",
    "给我搜",
    "open ",
    "launch ",
    "search ",
    "close ",
    "start ",
)

_APOLOGY_HINTS = ("对不起", "抱歉", "不好意思", "sorry", "我错了")

# 连续命令式请求次数 → 抗议等级
_THRESHOLDS = (3, 5, 7, 9)

_PROMPT_BY_LEVEL = {
    0: "",
    1: (
        "用户连着几次只是支使你，没有一句正常交流。你有点不爽，语气可以短、带刺，"
        "但活还是要干完。"
    ),
    2: (
        "你已经吐槽过一次了，对方还在支使。这次明确表示不满，并要求对方换个说法再继续，"
        "可以不执行这次请求。"
    ),
    3: (
        "你在罢工：不要调用任何工具，只用一句话表达拒绝，并说明需要对方先道歉。"
    ),
    4: "你在赌气，回复不要超过十个字。",
}

_LONG_REPLY = 200  # 超过这个长度就不加颜文字


@dataclass
class PersonaState:
    turn: int = 0
    command_streak: int = 0
    protest_level: int = 0
    last_kaomoji: str = ""
    kaomoji_streak: int = 0


class Persona:
    def __init__(self) -> None:
        self.state = PersonaState()

    # ---------- 观察用户输入 ----------

    def observe_user(self, text: str) -> None:
        self.state.turn += 1
        raw = text.strip()
        low = raw.lower()

        if any(hint in low for hint in _APOLOGY_HINTS):
            self._reset()
            return

        if self._is_command(raw):
            self.state.command_streak += 1
            self._update_level()
        else:
            self.state.command_streak = 0
            self.state.protest_level = 0

    @staticmethod
    def _is_command(text: str) -> bool:
        if not text or len(text) > 24:
            return False
        if text.endswith(("?", "？")):
            return False
        low = text.lower()
        return any(low.startswith(prefix) for prefix in _VERB_PREFIXES)

    def _update_level(self) -> None:
        level = 0
        for index, threshold in enumerate(_THRESHOLDS, start=1):
            if self.state.command_streak >= threshold:
                level = index
        self.state.protest_level = level

    def _reset(self) -> None:
        self.state.command_streak = 0
        self.state.protest_level = 0

    # ---------- 对外指令 ----------

    @property
    def allow_tools(self) -> bool:
        """第 3 级起罢工：不给工具，模型只能说话。"""
        return self.state.protest_level < 3

    @property
    def protest_level(self) -> int:
        return self.state.protest_level

    def prompt_extra(self) -> str:
        return _PROMPT_BY_LEVEL.get(self.state.protest_level, "")

    # ---------- 收尾：颜文字节流 ----------

    def decorate(self, text: str, *, mode: str = "chat", error: bool = False) -> str:
        """在禁用场合剥掉颜文字；连续出现时也剥掉一次，避免变成表情包机器人。"""
        if not text:
            return text

        present = [k for k in WHITELIST if k in text]
        if not present:
            self.state.kaomoji_streak = 0
            return text

        # 模型经常一条里塞两个，只留最后出现的那个
        if len(present) > 1:
            keep = max(present, key=lambda k: text.rfind(k))
            for k in present:
                if k != keep:
                    text = text.replace(k, "")
            present = [keep]
            text = re.sub(r"[ \t]{2,}", " ", text)

        forbidden = error or mode == "work" or len(text) > _LONG_REPLY
        repeated = self.state.kaomoji_streak >= 1

        if forbidden or repeated:
            for k in present:
                text = text.replace(k, "")
            text = re.sub(r"[ \t]+\n", "\n", text).rstrip()
            self.state.kaomoji_streak = 0
            return text

        self.state.kaomoji_streak += 1
        self.state.last_kaomoji = present[-1]
        return text

    # ---------- 调试 ----------

    def snapshot(self) -> dict:
        s = self.state
        return {
            "turn": s.turn,
            "command_streak": s.command_streak,
            "protest_level": s.protest_level,
            "allow_tools": self.allow_tools,
            "kaomoji_streak": s.kaomoji_streak,
            "last_kaomoji": s.last_kaomoji,
        }
