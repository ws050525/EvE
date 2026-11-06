"""work 档的文件工具：fs_read / fs_write / fs_list。

底线：**只能碰 work_root 之内**（默认就是项目根）。这是代码里的硬约束，不是提示词里的
一句"请不要越界"——提示词管不住模型的幻觉，路径检查管得住。

越界的判定顺序：
1. 相对路径按 work_root 拼，绝对路径原样用；
2. `Path.resolve()` 解开 `..` 和符号链接（否则链接可以指到根外面）；
3. 解析后的路径必须落在解析后的 work_root 之下。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec

DEFAULT_READ_CHARS = 12_000
MAX_READ_CHARS = 24_000
DEFAULT_LIST_ENTRIES = 120
MAX_LIST_ENTRIES = 400
MAX_WRITE_CHARS = 400_000

_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".tmp"}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _root_of(cfg) -> Path:
    root = getattr(cfg, "work_root", None) if cfg is not None else None
    path = Path(root) if root else _project_root()
    return path.resolve()


def _resolve(root: Path, raw: str) -> tuple[Path | None, str]:
    """返回 (解析后的路径, 错误)。错误非空时路径不可用。"""
    raw = (raw or "").strip()
    if not raw:
        return None, "缺少 path"

    candidate = Path(raw)
    target = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = target.resolve()
    except OSError as exc:  # 路径非法（过长、盘符不存在等）
        return None, f"路径无法解析：{exc}"

    if not _is_inside(root, resolved):
        return None, (
            f"拒绝访问：{resolved} 不在工作目录 {root} 之内。"
            "文件工具只允许操作工作目录里的东西。"
        )
    return resolved, ""


def _is_inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


# ---------- fs_read ----------


def fs_read(args: dict[str, Any], root: Path) -> str:
    path, error = _resolve(root, str(args.get("path", "")))
    if path is None:
        return f"[error] {error}"
    if path.is_dir():
        return f"[error] {_rel(root, path)} 是目录，用 fs_list 看目录内容"
    if not path.exists():
        return f"[error] 文件不存在：{_rel(root, path)}"

    try:
        raw = path.read_bytes()
    except OSError as exc:
        return f"[error] 读取失败：{exc}"

    if b"\x00" in raw[:4096]:
        return f"[error] {_rel(root, path)} 看起来是二进制文件（{len(raw)} 字节），不能按文本读"

    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    total = len(lines)

    try:
        start = max(1, int(args.get("offset", 1) or 1))
    except (TypeError, ValueError):
        start = 1
    try:
        limit = max(1, int(args.get("limit", 400) or 400))
    except (TypeError, ValueError):
        limit = 400
    try:
        max_chars = min(MAX_READ_CHARS, max(400, int(args.get("max_chars", DEFAULT_READ_CHARS) or DEFAULT_READ_CHARS)))
    except (TypeError, ValueError):
        max_chars = DEFAULT_READ_CHARS

    selected = lines[start - 1 : start - 1 + limit]
    if not selected:
        return f"[ok] {_rel(root, path)}（共 {total} 行，起始行 {start} 已超出末尾）"

    numbered: list[str] = []
    used = 0
    truncated = False
    for offset, line in enumerate(selected, start):
        piece = f"{offset:>5}\t{line}"
        if used + len(piece) + 1 > max_chars:
            truncated = True
            break
        numbered.append(piece)
        used += len(piece) + 1

    body = "\n".join(numbered)
    last = start - 1 + len(numbered)
    head = f"[ok] {_rel(root, path)}（共 {total} 行，显示 {start}-{last}）"
    if truncated or last < total:
        body += f"\n…（还有内容，用 offset={last + 1} 继续读）"
    return f"{head}\n{body}"


# ---------- fs_write ----------


def fs_write(args: dict[str, Any], root: Path) -> str:
    path, error = _resolve(root, str(args.get("path", "")))
    if path is None:
        return f"[error] {error}"
    if path.is_dir():
        return f"[error] {_rel(root, path)} 是目录，不能当文件写"

    content = args.get("content")
    if not isinstance(content, str):
        return "[error] 缺少 content（必须是字符串）"
    if len(content) > MAX_WRITE_CHARS:
        return f"[error] 内容太长（{len(content)} 字符，上限 {MAX_WRITE_CHARS}），请拆成多次写"

    mode = str(args.get("mode") or "overwrite").strip().lower()
    if mode not in {"overwrite", "append"}:
        return f"[error] mode 只能是 overwrite 或 append，收到 {mode!r}"

    existed = path.exists()
    previous_lines = 0
    if existed:
        try:
            previous_lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            previous_lines = 0

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
        else:
            # 直接截断重写：先写完再落盘，避免中途失败留下半个文件
            tmp = path.with_name(path.name + ".eve-tmp")
            tmp.write_text(content, encoding="utf-8", newline="\n")
            os.replace(tmp, path)
    except OSError as exc:
        return f"[error] 写入失败：{exc}"

    new_lines = len(content.splitlines())
    action = "追加" if mode == "append" else "写入"
    note = f"（原 {previous_lines} 行）" if existed else "（新建）"
    return (
        f"[ok] 已{action} {_rel(root, path)}{note}：{new_lines} 行 / {len(content)} 字符"
    )


# ---------- fs_list ----------


def fs_list(args: dict[str, Any], root: Path) -> str:
    path, error = _resolve(root, str(args.get("path", ".") or "."))
    if path is None:
        return f"[error] {error}"
    if not path.exists():
        return f"[error] 路径不存在：{_rel(root, path)}"
    if path.is_file():
        return f"[ok] {_rel(root, path)}：{_human_size(path.stat().st_size)}（这是一个文件）"

    try:
        recursive = bool(args.get("recursive", False))
    except (TypeError, ValueError):
        recursive = False
    try:
        max_entries = min(
            MAX_LIST_ENTRIES, max(1, int(args.get("max_entries", DEFAULT_LIST_ENTRIES) or DEFAULT_LIST_ENTRIES))
        )
    except (TypeError, ValueError):
        max_entries = DEFAULT_LIST_ENTRIES
    pattern = str(args.get("pattern") or "").strip()

    dirs: list[str] = []
    files: list[str] = []
    try:
        entries = _walk(path, recursive, pattern)
        for entry in entries:
            if len(dirs) + len(files) >= max_entries:
                return _render(root, path, dirs, files) + f"\n…（已到 {max_entries} 项上限，换更窄的路径再看）"
            if entry.is_dir():
                dirs.append(_rel(root, entry) + "/")
            else:
                try:
                    size = _human_size(entry.stat().st_size)
                except OSError:
                    size = "?"
                files.append(f"{_rel(root, entry)}  ({size})")
    except OSError as exc:
        return f"[error] 列目录失败：{exc}"

    if not dirs and not files:
        return f"[ok] {_rel(root, path)} 是空的（或没有匹配 {pattern!r} 的条目）"
    return _render(root, path, dirs, files)


def _walk(path: Path, recursive: bool, pattern: str):
    if not recursive:
        for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if entry.name in _SKIP_DIRS:
                continue
            if pattern and entry.is_file():
                if not entry.match(pattern):
                    continue
            yield entry
        return

    for base, subdirs, names in os.walk(path):
        subdirs[:] = sorted(d for d in subdirs if d not in _SKIP_DIRS)
        base_path = Path(base)
        yield from (base_path / d for d in subdirs)
        for name in sorted(names):
            if pattern and not Path(name).match(pattern):
                continue
            yield base_path / name


def _render(root: Path, path: Path, dirs: list[str], files: list[str]) -> str:
    lines = [f"[ok] {_rel(root, path)}：{len(dirs)} 个目录 / {len(files)} 个文件"]
    lines += [f"  {d}" for d in dirs]
    lines += [f"  {f}" for f in files]
    return "\n".join(lines)


# ---------- 注册 ----------


def register_workspace(registry: ToolRegistry, cfg=None) -> None:
    root = _root_of(cfg)

    registry.register(
        ToolSpec(
            name="fs_read",
            description=(
                f"读取文本文件并带行号返回。只能读工作目录 {root} 里面的文件。"
                "大文件用 offset/limit 分段读。改文件之前先读一遍。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对工作目录，或工作目录内的绝对路径）"},
                    "offset": {"type": "integer", "description": "从第几行开始，默认 1"},
                    "limit": {"type": "integer", "description": "最多读多少行，默认 400"},
                    "max_chars": {
                        "type": "integer",
                        "description": f"最多返回多少字符，默认 {DEFAULT_READ_CHARS}",
                    },
                },
                "required": ["path"],
            },
            handler=lambda args: fs_read(args, root),
            risk="low",
        )
    )

    registry.register(
        ToolSpec(
            name="fs_list",
            description=(
                f"列目录内容，目录在前、文件带大小。只能列工作目录 {root} 里面的路径。"
                "不确定文件在哪时先用它探路。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认工作目录根"},
                    "recursive": {"type": "boolean", "description": "是否递归，默认 false"},
                    "pattern": {"type": "string", "description": "文件名通配，例如 *.py"},
                    "max_entries": {"type": "integer", "description": "最多列多少项，默认 120"},
                },
            },
            handler=lambda args: fs_list(args, root),
            risk="low",
        )
    )

    registry.register(
        ToolSpec(
            name="fs_write",
            description=(
                f"写文本文件（覆盖或追加），目录不存在会自动创建。只能写工作目录 {root} 里面的路径。"
                "覆盖已有文件前必须先 fs_read 看过内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的完整内容"},
                    "mode": {
                        "type": "string",
                        "enum": ["overwrite", "append"],
                        "description": "overwrite 覆盖（默认）或 append 追加",
                    },
                },
                "required": ["path", "content"],
            },
            handler=lambda args: fs_write(args, root),
            risk="high",
        )
    )
