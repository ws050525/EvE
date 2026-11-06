"""work 档工具（fs_read / fs_write / fs_list / shell_exec）的单元测试。

重点是边界：越界必须被拒，底线黑名单必须拦住。不需要模型。

跑法： & 'D:\\python\\python.exe' tests\\test_workspace.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.tools.registry import build_default_registry
from eve_agent.tools.shell import blocked_reason, shell_exec
from eve_agent.tools.workspace import fs_list, fs_read, fs_write

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


class _Cfg:
    """给 registry 用的最小配置桩。"""

    def __init__(self, root: Path) -> None:
        self.work_root = root
        self.tool_output_chars = 6000
        self.data_dir = root / ".data"
        self.web_backend = "auto"


@case
def registry_exposes_work_tools():
    with tempfile.TemporaryDirectory() as tmp:
        registry = build_default_registry(_Cfg(Path(tmp)))
        for name in ("fs_read", "fs_write", "fs_list", "shell_exec"):
            assert registry.has(name), f"缺少工具 {name}"
        # 风险分级：写和跑命令是高危，读目录是低危
        assert registry.spec("fs_write").risk == "high"
        assert registry.spec("shell_exec").risk == "high"
        assert registry.spec("fs_read").risk == "low"
        assert registry.spec("fs_list").risk == "low"


@case
def write_then_read_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = fs_write({"path": "notes/a.txt", "content": "第一行\n第二行\n"}, root)
        assert out.startswith("[ok]"), out

        read = fs_read({"path": "notes/a.txt"}, root)
        assert "第一行" in read and "第二行" in read, read
        assert "共 2 行" in read, read
        # 带行号，模型才能精确引用
        assert "1\t第一行" in read, read


@case
def append_mode_adds_lines():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        fs_write({"path": "log.txt", "content": "a\n"}, root)
        fs_write({"path": "log.txt", "content": "b\n", "mode": "append"}, root)
        text = (root / "log.txt").read_text(encoding="utf-8")
        assert text == "a\nb\n", repr(text)


@case
def read_offset_and_limit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        body = "\n".join(f"L{i}" for i in range(1, 21))
        fs_write({"path": "lines.txt", "content": body}, root)
        out = fs_read({"path": "lines.txt", "offset": 5, "limit": 3}, root)
        assert "显示 5-7" in out, out
        assert "L5" in out and "L7" in out
        assert "L8\t" not in out, out


@case
def read_missing_file_is_an_error_not_a_crash():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = fs_read({"path": "nope.txt"}, root)
        assert out.startswith("[error]"), out


@case
def binary_file_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "blob.bin").write_bytes(b"\x00\x01\x02binary")
        out = fs_read({"path": "blob.bin"}, root)
        assert out.startswith("[error]") and "二进制" in out, out


@case
def escape_via_dotdot_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        root = (Path(tmp) / "work").resolve()
        root.mkdir()
        (Path(tmp) / "secret.txt").write_text("不该读到", encoding="utf-8")

        out = fs_read({"path": "../secret.txt"}, root)
        assert out.startswith("[error]") and "不在工作目录" in out, out

        out = fs_write({"path": "../outside.txt", "content": "x"}, root)
        assert out.startswith("[error]"), out
        assert not (Path(tmp) / "outside.txt").exists(), "越界写入必须真的没发生"


@case
def absolute_path_outside_root_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        root = (Path(tmp) / "work").resolve()
        root.mkdir()
        outside = (Path(tmp) / "elsewhere.txt").resolve()
        outside.write_text("x", encoding="utf-8")

        out = fs_read({"path": str(outside)}, root)
        assert out.startswith("[error]"), out


@case
def absolute_path_inside_root_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        target = root / "inside.txt"
        target.write_text("ok", encoding="utf-8")
        out = fs_read({"path": str(target)}, root)
        assert out.startswith("[ok]") and "ok" in out, out


@case
def list_shows_dirs_and_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "sub").mkdir()
        (root / "a.txt").write_text("hi", encoding="utf-8")

        out = fs_list({"path": "."}, root)
        assert "sub/" in out, out
        assert "a.txt" in out, out

        out = fs_list({"path": ".", "recursive": True, "pattern": "*.txt"}, root)
        assert "a.txt" in out, out


@case
def shell_denylist_blocks_dangerous_commands():
    for command in (
        "shutdown /s /t 0",
        "format C:",
        "diskpart",
        "del /f /s /q C:\\Windows",
        "rd /s /q C:\\temp",
        "reg delete HKLM\\Software\\Foo /f",
        "bcdedit /set {default} safeboot minimal",
        "rm -rf /",
    ):
        assert blocked_reason(command), f"应当拦截：{command}"


@case
def shell_denylist_allows_normal_commands():
    for command in (
        "python tests\\test_persona.py",
        "git status",
        "dir",
        "Get-Content README.md",
        "python -m pytest -q",
    ):
        assert not blocked_reason(command), f"不该拦截：{command}"


@case
def shell_denied_command_never_runs():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = shell_exec({"command": "shutdown /s /t 0"}, root)
        assert out.startswith("[denied]"), out


@case
def shell_runs_and_reports_exit_code():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = shell_exec({"command": "echo hello-eve"}, root)
        assert "exit=0" in out, out
        assert "hello-eve" in out, out


@case
def shell_cwd_cannot_escape():
    with tempfile.TemporaryDirectory() as tmp:
        root = (Path(tmp) / "work").resolve()
        root.mkdir()
        out = shell_exec({"command": "echo x", "cwd": ".."}, root)
        assert out.startswith("[error]") and "不在工作目录" in out, out


def main() -> int:
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
