"""程序启动：走开始菜单，而不是只认 PATH。

为什么要改：PATH 里只有命令行装过的程序。用户真正装的东西——微信、Adobe 全家桶、
各种国产客户端——基本都不在 PATH 里，但**几乎都会在开始菜单留一条**。
所以启动顺序是：别名 → 路径 → 开始菜单 → PATH。

数据来源两级：
1. `Get-StartApps`（PowerShell 自带，Win10+）：拿到开始菜单全部条目，包含 UWP 应用，
   实测这台机器 335 条；
2. 扫开始菜单目录里的 `.lnk` / `.url`：`Get-StartApps` 不可用时兜底。

AppID 有三种形态，启动方式不同：
- `D:\\...\\7zFM.exe`      → 直接执行
- `Microsoft.Office.ACCESS.EXE.15` → `explorer.exe shell:AppsFolder\\<AppID>`
- `{GUID}\\...\\x.exe`     → 同上（GUID 是已知文件夹的标识，shell 负责解析）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)

INDEX_TTL = 300.0  # 开始菜单索引缓存多久（秒）：装个新软件不用重启进程
PS_TIMEOUT = 60.0

_PS_EXE = os.path.join(
    os.environ.get("SystemRoot", r"C:\Windows"),
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe",
)

_PS_SCRIPT = (
    "$ProgressPreference='SilentlyContinue'; "
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
)

# 开始菜单里混着帮助文件、卸载器、说明文档，按名字粗略挡掉
_NOISE = re.compile(
    r"(卸载|uninstall|readme|许可证|许可协议|license|help|说明|帮助|"
    r"what's new|更新日志|release notes)",
    re.I,
)
_DOC_SUFFIXES = {".chm", ".txt", ".pdf", ".url", ".html", ".htm"}

_SPACE = re.compile(r"[\s\-_·.,，、()（）\[\]【】]+")


def _norm(text: str) -> str:
    """比对用的规范形：全角转半角、去大小写、去空白和常见分隔符。"""
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return _SPACE.sub("", text)


@dataclass(frozen=True)
class AppEntry:
    name: str
    app_id: str
    source: str = "start_menu"  # start_menu | shortcut

    @property
    def is_path(self) -> bool:
        return bool(re.match(r"^[A-Za-z]:[\\/]|^\\\\", self.app_id))


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse_start_apps(text: str) -> list[AppEntry]:
    """解析 Get-StartApps 的 JSON。单条目时 ConvertTo-Json 给的是对象不是数组。"""
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    entries: list[AppEntry] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        app_id = str(item.get("AppID") or "").strip()
        if name and app_id:
            entries.append(AppEntry(name=name, app_id=app_id, source="start_menu"))
    return entries


def query_start_apps(timeout: float = PS_TIMEOUT) -> list[AppEntry]:
    if not Path(_PS_EXE).exists():
        return []
    try:
        proc = subprocess.run(
            [_PS_EXE, "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return parse_start_apps(_decode(proc.stdout))


def shortcut_dirs() -> list[Path]:
    dirs = []
    for root in (os.environ.get("ProgramData"), os.environ.get("APPDATA")):
        if not root:
            continue
        path = Path(root) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if path.is_dir():
            dirs.append(path)
    return dirs


def scan_shortcuts(dirs: list[Path] | None = None) -> list[AppEntry]:
    """兜底：直接扫 .lnk / .url。文件名就是显示名，不需要解析快捷方式目标。"""
    found: list[AppEntry] = []
    for base in dirs if dirs is not None else shortcut_dirs():
        base = Path(base)
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() not in {".lnk", ".url"}:
                continue
            name = path.stem.strip()
            if not name:
                continue
            found.append(AppEntry(name=name, app_id=str(path), source="shortcut"))
    return found


def _looks_like_doc(entry: AppEntry) -> bool:
    return Path(entry.app_id).suffix.lower() in _DOC_SUFFIXES


def _is_noise(entry: AppEntry) -> bool:
    return bool(_NOISE.search(entry.name)) or _looks_like_doc(entry)


def rank(entries: list[AppEntry], query: str, limit: int = 8) -> list[AppEntry]:
    """按"像不像用户说的那个程序"排序。纯函数，方便单测。

    打分：完全相同 0 < 前缀 1 < 包含 2 < 被包含（用户多说几个字）3。
    同分时短名字优先，避免"微信"被"微信开发者工具"抢走。
    """
    q = _norm(query)
    if not q:
        return []

    scored: list[tuple[int, int, str, AppEntry]] = []
    for entry in entries:
        name = _norm(entry.name)
        if not name:
            continue
        if name == q:
            score = 0
        elif name.startswith(q):
            score = 1
        elif q in name:
            score = 2
        elif name in q:
            score = 3
        else:
            # 中文名对不上时，用目标里的可执行文件名再试一次
            exe = _norm(Path(entry.app_id).stem)
            if not exe or (exe != q and q not in exe and exe not in q):
                continue
            score = 4
        scored.append((score, len(name), name, entry))

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in scored[:limit]]


class AppIndex:
    """开始菜单索引。第一次用才加载，之后按 TTL 复用。"""

    def __init__(self, ttl: float = INDEX_TTL):
        self.ttl = ttl
        self._entries: list[AppEntry] | None = None
        self._loaded_at = 0.0
        self.source = ""

    def invalidate(self) -> None:
        self._entries = None
        self._loaded_at = 0.0

    def entries(self, refresh: bool = False) -> list[AppEntry]:
        fresh = self._entries is not None and (time.monotonic() - self._loaded_at) < self.ttl
        if fresh and not refresh:
            return self._entries or []

        entries = query_start_apps()
        source = "Get-StartApps"
        if not entries:
            entries = scan_shortcuts()
            source = "开始菜单目录"
        entries = _dedupe(entries)
        self._entries = entries
        self._loaded_at = time.monotonic()
        self.source = source if entries else ""
        return entries

    def find(self, query: str, limit: int = 8) -> list[AppEntry]:
        entries = self.entries()
        q = _norm(query)
        if not q:
            return []
        # 名字完全一致的，哪怕看着像卸载器 / 帮助文档也认——用户就是这么叫的
        exact = [e for e in entries if _norm(e.name) == q]
        if exact:
            return exact[:limit]
        candidates = [e for e in entries if not _is_noise(e)]
        return rank(candidates, query, limit)

    def search(self, query: str = "", limit: int = 40) -> list[AppEntry]:
        """给人看的清单：有 query 就模糊搜，没 query 就按名字列前 N 个（不含噪音）。

        和 `find` 的区别：这里**不**做"完全匹配就短路"。查清单是为了看见候选，
        只返回一个"微信"会把"微信开发者工具"藏起来。
        """
        entries = self.entries()
        clean = [e for e in entries if not _is_noise(e)]
        if query.strip():
            return rank(clean, query, limit)
        clean.sort(key=lambda e: _norm(e.name))
        return clean[:limit]


def _dedupe(entries: list[AppEntry]) -> list[AppEntry]:
    seen = set()
    out = []
    for entry in entries:
        key = (_norm(entry.name), entry.app_id.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def launch_entry(entry: AppEntry) -> str:
    """真正去启动。返回一句给模型看的结果；失败抛异常由上层包成 [error]。"""
    app_id = entry.app_id
    if entry.source == "shortcut" or (entry.is_path and Path(app_id).exists()):
        os.startfile(app_id)  # noqa: S606 - Windows 上就是用它
        return f"{entry.name}（{app_id}）"

    # 打包应用 / GUID 前缀路径：交给 shell 解析
    subprocess.Popen(
        ["explorer.exe", f"shell:AppsFolder\\{app_id}"],
        close_fds=True,
        creationflags=DETACHED_PROCESS,
    )
    return f"{entry.name}（{app_id}）"


_default_index = AppIndex()


def default_index() -> AppIndex:
    return _default_index
