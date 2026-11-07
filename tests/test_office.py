"""文档生成工具的测试：产出必须是真的能打开的文件。

跑法： & 'D:\\python\\python.exe' tests\\test_office.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.tools.office import office_write

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


SLIDES = [
    {"heading": "结论", "bullets": ["营收 +12%", "成本 -4%"]},
    {"heading": "下一步", "body": "统一模板。\n每周复盘。"},
]


@case
def makes_a_real_pptx():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = office_write(
            {
                "kind": "pptx",
                "path": "报告/季度.pptx",
                "title": "季度总结",
                "subtitle": "测试",
                "sections": SLIDES,
            },
            root,
        )
        assert out.startswith("[ok]"), out
        path = root / "报告" / "季度.pptx"
        assert path.exists() and path.stat().st_size > 5000, path

        from pptx import Presentation

        prs = Presentation(str(path))
        assert len(prs.slides) == 3, len(prs.slides)  # 封面 + 两页
        titles = [
            s.shapes.title.text for s in prs.slides if s.shapes.title is not None
        ]
        assert "季度总结" in titles and "结论" in titles, titles


@case
def makes_a_real_docx():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = office_write(
            {
                "kind": "docx",
                "path": "周报.docx",
                "title": "本周周报",
                "sections": [
                    {"heading": "完成", "bullets": ["写了裁剪", "修了播报"]},
                    {"heading": "风险", "body": "模型体积偏大。"},
                ],
            },
            root,
        )
        assert out.startswith("[ok]"), out

        from docx import Document

        doc = Document(str(root / "周报.docx"))
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "本周周报" in text and "写了裁剪" in text and "模型体积偏大" in text, text


@case
def makes_a_real_xlsx():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = office_write(
            {
                "kind": "xlsx",
                "path": "清单.xlsx",
                "title": "文件清单",
                "rows": [["名字", "大小"], ["a.txt", 12]],
            },
            root,
        )
        assert out.startswith("[ok]"), out

        from openpyxl import load_workbook

        ws = load_workbook(str(root / "清单.xlsx")).active
        assert ws.max_row == 2 and ws.max_column == 2, (ws.max_row, ws.max_column)
        assert ws.cell(1, 1).value == "名字"


@case
def suffix_is_filled_in_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        office_write({"kind": "docx", "path": "无后缀", "title": "x", "sections": []}, root)
        assert (root / "无后缀.docx").exists()


@case
def mismatched_suffix_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = office_write({"kind": "docx", "path": "错.pptx"}, root)
        assert out.startswith("[error]") and "对不上" in out, out


@case
def unknown_kind_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        out = office_write({"kind": "pdf", "path": "x.pdf"}, Path(tmp).resolve())
        assert out.startswith("[error]"), out


@case
def escape_outside_root_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        root = (Path(tmp) / "work").resolve()
        root.mkdir()
        out = office_write({"kind": "docx", "path": "../逃逸.docx", "title": "x"}, root)
        assert out.startswith("[error]"), out
        assert not (Path(tmp) / "逃逸.docx").exists()


@case
def empty_sections_still_produces_a_file():
    """只给标题也得能出文件，不能崩。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        out = office_write({"kind": "pptx", "path": "空.pptx", "title": "只有封面"}, root)
        assert out.startswith("[ok]"), out
        from pptx import Presentation

        prs = Presentation(str(root / "空.pptx"))
        assert len(prs.slides) == 1


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
