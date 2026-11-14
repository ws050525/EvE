"""多文件项目工具：一整套互相引用的资料。

为什么需要它：用户说"做一套资料：主报告 + 附录 + 一页摘要 PPT"，
模型单独写三个文件也能写，但常见问题是：

- 写到第三篇时忘了第一篇叫什么、放在哪，引用写成 `附录.md` 而实际是 `appendix.md`；
- 引用了不存在的文件，或者文件建了却没在任何地方被引用（孤儿）；
- 目录里多出一堆 `未命名1.docx` 这种没人认领的产物。

所以给它一个**清单（manifest）+ 自检**：开写之前先把"有哪些文件、各干什么"定下来，
写完再跑一遍 `project_status` 检查存在性、引用和孤儿。
清单本身就是跨文件的一致性来源——每篇开头都能看到整套资料的结构。

当前只做文本层的引用检查（markdown 链接）。Office 文件之间只按清单核对存在性，
不去解析 docx 里的引用文字——那属于"内容对不对"，不是工具能判的。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec
from .workspace import _rel, _resolve, _root_of

MANIFEST = "manifest.json"
INDEX = "index.md"
MAX_FILES = 30

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_CODEBLOCK = re.compile(r"```.*?```", re.S)


def _clean_target(target: str) -> str:
    target = target.split("#", 1)[0].strip()
    if target.startswith(("http://", "https://", "mailto:")):
        return ""
    return target


def _count_cjk(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def _read_manifest(folder: Path) -> dict[str, Any] | None:
    path = folder / MANIFEST
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _render_index(title: str, files: list[dict[str, str]]) -> str:
    lines = [f"# {title}", "", "本套资料的组成：", ""]
    for item in files:
        path = item.get("path") or ""
        purpose = item.get("purpose") or ""
        suffix = "" if path.lower().endswith((".md", ".txt")) else "（Office 文件，不在浏览器里预览）"
        lines.append(f"- [{path}]({path}) — {purpose}{suffix}")
    lines.append("")
    return "\n".join(lines)


def project_init(args: dict[str, Any], root: Path) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return "[error] 缺少 name（项目目录，例如 docs/ai-report）"

    folder, error = _resolve(root, name)
    if folder is None:
        return f"[error] {error}"
    if folder.exists() and (folder / MANIFEST).exists() and not args.get("overwrite"):
        return (
            f"[error] {_rel(root, folder)} 已经是一套资料了（有 {MANIFEST}）。"
            "要重做就先删掉，或者传 overwrite=true。"
        )

    raw_files = args.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        return "[error] files 必须是非空数组，每项 {path, purpose}"

    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw_files[:MAX_FILES]:
        if isinstance(entry, str):
            path, purpose = entry, ""
        elif isinstance(entry, dict):
            path = str(entry.get("path") or "").strip()
            purpose = str(entry.get("purpose") or "").strip()
        else:
            continue
        if not path or path in seen:
            continue
        if ".." in Path(path).parts:
            return f"[error] 文件路径不能出项目目录：{path}"
        seen.add(path)
        files.append({"path": path, "purpose": purpose})

    if not files:
        return "[error] files 里没有有效的 path"

    title = str(args.get("title") or "").strip() or folder.name
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / MANIFEST).write_text(
            json.dumps(
                {"version": 1, "title": title, "files": files}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        index_path = folder / INDEX
        if not index_path.exists():
            index_path.write_text(_render_index(title, files), encoding="utf-8")
    except OSError as exc:
        return f"[error] 建目录失败：{exc}"

    lines = [f"[ok] 资料项目：{_rel(root, folder)}（{len(files)} 个文件）"]
    for item in files:
        lines.append(f"  - {item['path']}：{item['purpose'] or '（未写用途）'}")
    lines.append("")
    lines.append(
        "下一步：按清单逐个写文件（fs_write / office_write），"
        "每篇开头写清楚和别的文件的引用关系；全部写完后调 project_status 自检。"
    )
    return "\n".join(lines)


def project_status(args: dict[str, Any], root: Path) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return "[error] 缺少 name"
    folder, error = _resolve(root, name)
    if folder is None:
        return f"[error] {error}"
    if not folder.is_dir():
        return f"[error] 目录不存在：{_rel(root, folder)}"

    manifest = _read_manifest(folder)
    if manifest is None:
        return f"[error] {_rel(root, folder)} 里没有 {MANIFEST}，先用 project_init 建清单"

    entries = [item for item in manifest.get("files") or [] if isinstance(item, dict)]
    lines = [f"[项目] {_rel(root, folder)} · {manifest.get('title') or folder.name}"]
    missing: list[str] = []
    empty: list[str] = []
    planned: set[str] = set()

    for item in entries:
        rel = str(item.get("path") or "")
        planned.add(rel)
        path = folder / rel
        if not path.exists():
            missing.append(rel)
            lines.append(f"  [ ] {rel}  — 还没写")
            continue
        size = path.stat().st_size
        note = f"{size / 1024:.1f} KB"
        if rel.lower().endswith((".md", ".txt")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                chars = _count_cjk(text)
                note += f"，{chars} 字"
                if chars < 50:
                    empty.append(rel)
            except OSError:
                pass
        mark = "!" if rel in empty else "x"
        lines.append(f"  [{mark}] {rel}  — {note}")

    # 引用检查：只查 markdown 里的相对链接
    broken: list[tuple[str, str]] = []
    for item in entries:
        rel = str(item.get("path") or "")
        if not rel.lower().endswith((".md", ".txt")):
            continue
        path = folder / rel
        if not path.exists():
            continue
        try:
            text = _CODEBLOCK.sub("", path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for target in _LINK.findall(text):
            clean = _clean_target(target)
            if not clean:
                continue
            if not (folder / clean).exists():
                broken.append((rel, clean))

    # 孤儿：目录里有、清单里没有的（跳过清单/索引本身）
    known = planned | {MANIFEST, INDEX}
    orphans: list[str] = []
    for path in sorted(folder.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(folder).as_posix()
        if rel not in known:
            orphans.append(rel)

    if missing:
        lines.append("缺文件：" + "、".join(missing))
    if empty:
        lines.append("内容过少（<50 字）：" + "、".join(empty))
    if broken:
        pairs = [f"{src} → {dst}" for src, dst in broken[:8]]
        lines.append("引用指向不存在的文件：" + "；".join(pairs))
    if orphans:
        lines.append("清单里没有的文件（可能是废弃产物）：" + "、".join(orphans[:8]))

    complete = not missing and not broken
    lines.append("")
    lines.append(
        "[ok] 这套资料齐了，引用也都指得通。"
        if complete
        else "[!] 还没齐，按上面的缺项补完再交付。"
    )
    return "\n".join(lines)


def register_project(registry: ToolRegistry, cfg=None) -> None:
    root = _root_of(cfg)

    registry.register(
        ToolSpec(
            name="project_init",
            description=(
                "开一套多文件资料：先定清单（有哪些文件、各干什么），生成 manifest.json 和 index.md。"
                "用户要“一套资料 / 主报告加附录 / 报告配 PPT”时先调它，然后再逐个写文件。"
                "files 每项 {path, purpose}，path 相对项目目录。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "项目目录（相对工作目录），例如 docs/ai-report",
                    },
                    "title": {"type": "string", "description": "这套资料的标题"},
                    "files": {
                        "type": "array",
                        "description": "文件清单，按阅读顺序",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "文件名，如 01-主报告.md"},
                                "purpose": {"type": "string", "description": "这篇写什么"},
                            },
                            "required": ["path"],
                        },
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "已经有一套时是否重做清单",
                    },
                },
                "required": ["name", "files"],
            },
            handler=lambda args: project_init(args, root),
            risk="medium",
        )
    )

    registry.register(
        ToolSpec(
            name="project_status",
            description=(
                "检查一套资料的完成度：哪些文件还没写、内容是否过少、"
                "markdown 里的相对链接指向的文件是否存在、有没有清单外的多余文件。"
                "交付之前必须跑一次，把缺的补齐。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "项目目录，例如 docs/ai-report"}
                },
                "required": ["name"],
            },
            handler=lambda args: project_status(args, root),
            risk="low",
        )
    )
