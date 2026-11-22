"""灵魂文件：记住"这个人是谁、他怎么说话"，让简写指令也能被执行。

为什么要它：用户不会每次都写标准指令。他说「搜下晴天」，期望的是"网易云搜并播放"，
而不是"我只看到两个字，请给完整命令"。

`soul/SOUL.md` 是**用户手写**的（称呼、习惯、简写约定、禁忌），
`.data/soul_learned.md` 是**伊芙自己攒的**（用 `soul_remember` 追加）。
两份都会拼进每次的 system prompt，而且排在档位提示词前面——**灵魂优先**。

设计上只做三件事：读、拼、追加。不做向量检索，不猜语义——宁可少记，不要记错。
"""

from __future__ import annotations

import time
from pathlib import Path

DEFAULT_SOUL = "soul/SOUL.md"
LEARNED_FILE = "soul_learned.md"
MAX_LEARNED = 40  # 学到的偏好最多留这么多条，防止越堆越长
MAX_LEARNED_CHARS = 160  # 单条上限


class Soul:
    def __init__(self, cfg):
        root = Path(getattr(cfg, "root", "."))
        path = Path(getattr(cfg, "soul_file", DEFAULT_SOUL) or DEFAULT_SOUL)
        self.path = path if path.is_absolute() else root / path
        self.data_dir = Path(getattr(cfg, "data_dir", ".data"))
        self.learned_path = self.data_dir / LEARNED_FILE

    # ---------- 读 ----------

    def text(self) -> str:
        """手写那份。"""
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def learned(self) -> list[str]:
        try:
            raw = self.learned_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [line[2:].strip() for line in raw if line.startswith("- ") and line[2:].strip()]

    def prompt_block(self, chars: int = 1200) -> str:
        """拼进 system prompt 的那一段。没内容就返回空串。"""
        parts = []
        body = self.text()
        if body:
            parts.append(body[:chars])
        facts = self.learned()
        if facts:
            lines = "\n".join(f"- {fact}" for fact in facts[-12:])
            parts.append(f"## 我在日常里记下的（你自己攒的）\n{lines}")
        if not parts:
            return ""
        head = "## 灵魂文件（最优先：与下面任何档位说明冲突时，以这里为准）"
        return head + "\n\n" + "\n\n".join(parts)

    def exists(self) -> bool:
        return self.path.exists() or bool(self.learned())

    # ---------- 写 ----------

    def remember(self, fact: str) -> tuple[bool, str]:
        fact = " ".join((fact or "").split())[:MAX_LEARNED_CHARS]
        if len(fact) < 4:
            return False, "太短了，写完整一点（比如「他说『搜下』时指的是网易云搜歌」）"
        facts = self.learned()
        if fact in facts:
            return False, "这条已经记过了"
        facts.append(fact)
        facts = facts[-MAX_LEARNED:]
        self.learned_path.parent.mkdir(parents=True, exist_ok=True)
        body = "# 伊芙自己记下的偏好（可以直接编辑或删除）\n" + "\n".join(
            f"- {item}" for item in facts
        )
        self.learned_path.write_text(body + "\n", encoding="utf-8")
        return True, f"记住了：{fact}"

    def forget(self, keyword: str) -> int:
        keyword = (keyword or "").strip()
        if not keyword:
            return 0
        facts = self.learned()
        kept = [fact for fact in facts if keyword not in fact]
        removed = len(facts) - len(kept)
        if removed:
            self.learned_path.write_text(
                "# 伊芙自己记下的偏好（可以直接编辑或删除）\n"
                + "\n".join(f"- {item}" for item in kept)
                + "\n",
                encoding="utf-8",
            )
        return removed

    def stats(self) -> dict:
        return {
            "soul": str(self.path),
            "soul_chars": len(self.text()),
            "learned": len(self.learned()),
            "now": time.strftime("%Y-%m-%d %H:%M"),
        }
