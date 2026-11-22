"""语义动作记忆：把"做过的操作"记成意图，下次直接回放。

为什么不记坐标：坐标是硬编码，换个窗口位置、换台显示器、软件改个版就全废。
这里记的是**意图**：

    {"do": "click", "target": "播放全部"}
    {"do": "type",  "target": "搜索框", "text": "晴天"}

回放时用 `ui_find(target)` 重新定位（OCR 10–15px），所以窗口挪了、分辨率变了都还能用；
只有"界面上再也找不到这几个字"才会失效。

收益：一次典型桌面任务要 20–33 步（每步一次模型往返），回放时这些步骤全不需要模型，
只在最后验证一次。命中就省掉整条思考链。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STORE_FILE = "macros.json"
MAX_MACROS = 40
MIN_SCORE = 4  # 关键词总长度要达到这个值才算命中，防止一个"播放"就把别的任务也吃进来

# 带这些词的任务是"一串事"，不该被单条记忆吃掉（实测抢过一个三件事的复合任务，
# 把后面整段指令当成歌名输进了搜索框）
COMPOUND_HINTS = ("然后", "接着", "再打", "再去", "最后", "首先", "其次", "第一", "第二", "第三",
                  "并且", "同时", "顺便", "三件事", "两件事")


@dataclass
class Step:
    """一步语义动作。"""

    do: str  # launch / focus / click / type / key / wait
    target: str = ""  # click / type 时：控件上的文字
    text: str = ""  # type 时：要输入的内容
    keys: str = ""  # key 时：组合键
    app: str = ""  # launch 时：程序名
    window: str = ""  # focus 时：窗口标题
    # 没有文字的控件（搜索框）没法用 OCR 找，就记"在窗口里的相对位置"（0–1）。
    # 相对坐标比绝对坐标稳：窗口挪了、大小变了都还对；只有布局改了才失效。
    point: str = ""  # 形如 "0.55,0.03"

    def describe(self) -> str:
        if self.do == "launch":
            return f"打开 {self.app}"
        if self.do == "focus":
            return f"切到 {self.window}"
        if self.do == "click":
            return f"点「{self.target}」"
        if self.do == "click_point":
            return f"点窗口的 ({self.point}) 位置"
        if self.do == "type":
            where = f"在「{self.target}」" if self.target else "在当前输入框"
            return f"{where}输入 {self.text!r}"
        if self.do == "key":
            return f"按键 {self.keys}"
        return f"等 {self.text or '一会儿'}"


@dataclass
class Macro:
    """一条可回放的操作序列。"""

    id: str
    name: str
    keywords: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    verify: str = ""  # 回放完用一句话确认（丢给 screen_read）
    # 参数提取用的正则：第一个捕获组填 {1}，第二个填 {2}……
    # 有 patterns 但一个都没匹配上时**不硬放**，交回给模型（否则会照着旧参数乱做）
    patterns: list[str] = field(default_factory=list)
    created: str = ""
    hits: int = 0
    last_ok: str = ""

    def summary(self) -> str:
        chain = " → ".join(step.describe() for step in self.steps)
        return f"{self.name}：{chain}"


class MacroStore:
    """读写 `.data/macros.json`。"""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / STORE_FILE
        self.macros: list[Macro] = self._load()

    # ---------- 持久化 ----------

    def _load(self) -> list[Macro]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        macros = []
        for item in raw.get("macros", []):
            try:
                steps = [Step(**step) for step in item.get("steps", [])]
                macros.append(
                    Macro(
                        id=item["id"],
                        name=item.get("name", ""),
                        keywords=item.get("keywords", []),
                        steps=steps,
                        verify=item.get("verify", ""),
                        patterns=item.get("patterns", []),
                        created=item.get("created", ""),
                        hits=int(item.get("hits", 0)),
                        last_ok=item.get("last_ok", ""),
                    )
                )
            except (KeyError, TypeError):
                continue
        return macros

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "macros": [
                {**asdict(macro)} for macro in self.macros[:MAX_MACROS]
            ]
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 增删查 ----------

    def add(
        self,
        name: str,
        keywords: list[str],
        steps: list[dict],
        verify: str = "",
        patterns: list[str] | None = None,
    ) -> Macro:
        macro = Macro(
            id=f"macro-{uuid.uuid4().hex[:6]}",
            name=name,
            keywords=[k for k in keywords if k],
            steps=[Step(**step) for step in steps],
            verify=verify,
            patterns=list(patterns or []),
            created=time.strftime("%Y-%m-%d %H:%M"),
        )
        self.macros.insert(0, macro)
        self.macros = self.macros[:MAX_MACROS]
        self.save()
        return macro

    def forget(self, name_or_id: str) -> bool:
        before = len(self.macros)
        self.macros = [
            m for m in self.macros if name_or_id not in (m.id, m.name)
        ]
        if len(self.macros) != before:
            self.save()
            return True
        return False

    def list_all(self) -> list[Macro]:
        return list(self.macros)

    def get(self, name_or_id: str) -> Macro | None:
        for macro in self.macros:
            if name_or_id in (macro.id, macro.name):
                return macro
        return None

    # ---------- 匹配 ----------

    def match(self, text: str) -> tuple[Macro | None, int]:
        """按关键词找最像的一条。返回 (宏, 得分)。"""
        text = (text or "").strip()
        if not text:
            return None, 0
        if any(word in text for word in COMPOUND_HINTS):
            return None, 0  # 复合任务不抢，让模型自己拆
        best, best_score = None, 0
        for macro in self.macros:
            score = sum(len(k) for k in macro.keywords if k and k in text)
            if score > best_score:
                best, best_score = macro, score
        if best is None:
            return None, 0
        # 短指令（"搜下稻香"）关键词只有两个字，但意图很明确：短句里命中一个就算
        short_and_clear = len(text) <= 12 and best_score > 0
        if not short_and_clear and best_score < MIN_SCORE:
            return None, best_score
        return best, best_score

    def record_hit(self, macro: Macro, ok: bool) -> None:
        macro.hits += 1
        macro.last_ok = time.strftime("%Y-%m-%d %H:%M") if ok else macro.last_ok
        self.save()


# ---------------------------------------------------------------------------
# 回放：把语义动作翻译成工具调用
# ---------------------------------------------------------------------------


def _window_by_title(name: str) -> tuple[str, tuple[int, int, int, int]] | None:
    """按标题找窗口，取同标题里面积最大的那个。"""
    try:
        from eve_agent.tools import desktop as desktop_tools
        from eve_core import vision

        best, best_area = None, 0
        for win in desktop_tools._enum_windows():
            if name and name in win["title"]:
                info = vision.window_info(win["hwnd"])
                if info is None:
                    continue
                left, top, right, bottom = info[1]
                area = (right - left) * (bottom - top)
                if area > best_area:
                    best, best_area = (win["hwnd"], info), area
        if best is not None:
            desktop_tools.set_target(best[0], best[1][0])
            return best[1]
    except Exception:
        return None
    return None


def fill_slots(macro: Macro, text: str) -> dict[str, str]:
    """按 macro.patterns 从任务里抽参数，填成 {"1": .., "2": ..}。"""
    import re

    values: dict[str, str] = {}
    for pattern in macro.patterns:
        try:
            match = re.search(pattern, text or "")
        except re.error:
            continue
        if not match:
            continue
        for index, group in enumerate(match.groups(), 1):
            value = (group or "").strip()
            # 不合格的"参数"宁可不要：否则会把整段指令当歌名输进去（踩过）
            if not value or len(value) > 24:
                continue
            if any(ch in value for ch in "\\/:：;；,，。\n\t"):
                continue
            values[str(index)] = value
        if values:
            break
    return values


def _fill(template: str, values: dict[str, str]) -> str:
    out = template or ""
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def replay(
    macro: Macro,
    dispatch,
    allowed: set[str],
    log=None,
    values: dict[str, str] | None = None,
) -> tuple[bool, list[str]]:
    """逐步执行。`dispatch(name, args)` 由调用方提供（已绑定确认策略）。

    返回 (是否全部成功, 每一步的结果文字)。
    """
    results: list[str] = []

    def call(tool: str, args: dict[str, Any]) -> str:
        if tool not in allowed:
            return f"[error] 工具 {tool} 不在当前档位里"
        return dispatch(tool, args)

    def click_point(point: str) -> str:
        """按"窗口相对位置"点击：给没有文字、也没有纯色的控件兜底。"""
        try:
            from eve_agent.tools import desktop as desktop_tools
            from eve_core import vision

            frac_x, frac_y = [float(v) for v in str(point).replace("，", ",").split(",")[:2]]
        except Exception:
            return f"[error] point 格式要是 '0.5,0.3' 这样的比例：{point!r}"
        hwnd, _title = desktop_tools.get_target()
        info = vision.window_info(int(hwnd or 0))
        if info is None:
            # 没记录目标窗口（比如 app_launch 没抓到新窗口）就用当前前台窗口
            foreground = vision.foreground_window()
            if foreground is not None:
                info = (foreground[1], foreground[2])
                desktop_tools.set_target(foreground[0], foreground[1])
        if info is None and launched_app:
            # 目标窗口没了（启动时抓到的是闪屏，或者被 esc 关掉了）：按程序名再找一次
            info = _window_by_title(launched_app)
        if info is None:
            return "[error] 拿不到目标窗口的位置（没有可用的前台窗口）"
        _title, (left, top, right, bottom) = info
        x = int(left + frac_x * (right - left))
        y = int(top + frac_y * (bottom - top))
        return call("mouse_click", {"x": x, "y": y})

    def find_and_click(target: str) -> str:
        found = call("ui_find", {"target": target})
        if found.startswith("[error]"):
            return found
        # ui_find 的返回里带 "mouse_click(x, y)" 提示，直接用它给的坐标最稳
        import re

        match = re.search(r"mouse_click\((\d+),\s*(\d+)\)", found)
        if not match:
            return f"[error] ui_find 没给出可点坐标：{found[:80]}"
        return call("mouse_click", {"x": int(match.group(1)), "y": int(match.group(2))})

    values = values or {}
    launched_app = ""
    for step in macro.steps:
        if step.do == "launch":
            out = call("app_launch", {"target": step.app})
            if not out.startswith(("[error]", "[denied]")):
                launched_app = step.app
        elif step.do == "focus":
            out = call("window_focus", {"title": step.window or step.target})
        elif step.do == "click":
            out = find_and_click(step.target)
        elif step.do == "click_point":
            out = click_point(step.point)
        elif step.do == "type":
            # 注意：不能就地改 step.text —— record_hit() 会把内存里的 steps 存回磁盘，
            # 那样参数就被固化死，下次换歌名也只会输旧的那个（踩过）
            text_to_type = _fill(step.text, values)
            if not step.target:
                out = call("key_type", {"text": text_to_type})
            else:
                located = find_and_click(step.target)
                out = located if located.startswith("[error]") else call(
                    "key_type", {"text": text_to_type}
                )
        elif step.do == "key":
            out = call("key_hotkey", {"keys": step.keys})
        elif step.do == "wait":
            time.sleep(float(step.text or 1))
            out = "[ok] 等待完成"
        else:
            out = f"[error] 不认识的动作 {step.do}"
        failed = out.startswith("[error]") or out.startswith("[denied]")
        results.append(f"{step.describe()} → {'ok' if not failed else out[:60]}")
        if log is not None:
            log(step, out)
        if failed:
            return False, results
    return True, results
