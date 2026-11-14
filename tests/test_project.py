"""多文件资料的测试：清单、引用、孤儿、完成度。

跑法： & 'D:\\python\\python.exe' tests\\test_project.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.tools.project import MANIFEST, project_init, project_status

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


FILES = [
    {"path": "01-主报告.md", "purpose": "总体结论"},
    {"path": "02-附录.md", "purpose": "数据明细"},
]


@case
def init_creates_manifest_and_index():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = project_init({"name": "docs/report", "title": "季度复盘", "files": FILES}, root)
        assert out.startswith("[ok]"), out

        folder = root / "docs" / "report"
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        assert manifest["title"] == "季度复盘"
        assert [item["path"] for item in manifest["files"]] == ["01-主报告.md", "02-附录.md"]

        index = (folder / "index.md").read_text(encoding="utf-8")
        assert "季度复盘" in index
        assert "[01-主报告.md](01-主报告.md)" in index, index


@case
def status_lists_what_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        project_init({"name": "p", "title": "T", "files": FILES}, root)
        out = project_status({"name": "p"}, root)
        assert "还没写" in out, out
        assert "缺文件" in out, out
        assert "还剩" not in out


@case
def status_detects_broken_links():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        project_init({"name": "p", "title": "T", "files": FILES}, root)
        folder = root / "p"
        (folder / "01-主报告.md").write_text(
            "# 主报告\n\n" + "正文" * 40 + "\n详见 [附录](02-附录.md) 和 [不存在的文件](03-数据.md)。\n",
            encoding="utf-8",
        )
        (folder / "02-附录.md").write_text("# 附录\n\n" + "数据" * 40 + "\n", encoding="utf-8")

        out = project_status({"name": "p"}, root)
        assert "引用指向不存在的文件" in out, out
        broken_part = out.split("引用指向不存在的文件")[1]
        assert "03-数据.md" in broken_part, out
        assert "02-附录.md" not in broken_part, "指向存在文件的链接不该被报成断链"


@case
def status_detects_orphan_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        project_init({"name": "p", "title": "T", "files": FILES}, root)
        folder = root / "p"
        for item in FILES:
            (folder / item["path"]).write_text("# x\n\n" + "内容" * 60, encoding="utf-8")
        (folder / "未命名1.docx").write_bytes(b"x")

        out = project_status({"name": "p"}, root)
        assert "未命名1.docx" in out, out


@case
def complete_project_passes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        project_init({"name": "p", "title": "T", "files": FILES}, root)
        folder = root / "p"
        (folder / "01-主报告.md").write_text(
            "# 主报告\n\n" + "结论" * 40 + "\n数据见 [附录](02-附录.md)。\n", encoding="utf-8"
        )
        (folder / "02-附录.md").write_text("# 附录\n\n" + "明细" * 40 + "\n", encoding="utf-8")

        out = project_status({"name": "p"}, root)
        assert "这套资料齐了" in out, out


@case
def init_refuses_to_overwrite_without_flag():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        project_init({"name": "p", "title": "T", "files": FILES}, root)
        again = project_init({"name": "p", "title": "T2", "files": FILES}, root)
        assert again.startswith("[error]") and "已经是一套资料" in again, again

        forced = project_init(
            {"name": "p", "title": "T2", "files": FILES, "overwrite": True}, root
        )
        assert forced.startswith("[ok]"), forced
        manifest = json.loads((root / "p" / MANIFEST).read_text(encoding="utf-8"))
        assert manifest["title"] == "T2"


@case
def init_rejects_paths_escaping_the_project():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = project_init(
            {"name": "p", "files": [{"path": "../跑出去了.md", "purpose": "x"}]}, root
        )
        assert out.startswith("[error]") and "不能出项目目录" in out, out


@case
def status_needs_a_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "空目录").mkdir()
        out = project_status({"name": "空目录"}, root)
        assert out.startswith("[error]") and "project_init" in out, out


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
