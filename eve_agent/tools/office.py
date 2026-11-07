"""文档生成工具：直接写出 .pptx / .docx / .xlsx 文件。

为什么要有这个：让模型"写代码生成 PPT"，它就会把一段 python-pptx 脚本甩给用户，
用户还得自己跑。实测让 work 档"写一份 PPT"，返回的就是代码。
所以要给它一个**直接产出文件**的工具，路径给进去、内容给进去，文件就出来了。

内容用结构化参数传（标题 + 小节 + 要点），不让模型拼 XML 或写脚本。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec
from .workspace import _rel, _resolve, _root_of

MAX_SECTIONS = 30
FONT = "Microsoft YaHei"


def _set_font(run) -> None:
    run.font.name = FONT
    # 中文字体要单独写在 rPr 的 ea 上，否则 PowerPoint 会退回宋体
    try:
        from pptx.oxml.ns import qn

        rpr = run._r.get_or_add_rPr()
        ea = rpr.find(qn("a:ea"))
        if ea is None:
            ea = rpr.makeelement(qn("a:ea"), {})
            rpr.append(ea)
        ea.set("typeface", FONT)
    except Exception:
        pass


def _sections(args: dict[str, Any]) -> list[dict[str, Any]]:
    raw = args.get("sections")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_SECTIONS]:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str):
            out.append({"heading": "", "body": item})
    return out


def _write_pptx(path: Path, title: str, subtitle: str, sections: list[dict]) -> str:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = title or "未命名"
    if len(cover.placeholders) > 1:
        cover.placeholders[1].text = subtitle
    for shape in cover.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    _set_font(run)

    for section in sections:
        heading = str(section.get("heading") or "").strip()
        bullets = section.get("bullets") or []
        body = str(section.get("body") or "").strip()
        if not heading and not bullets and not body:
            continue

        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = heading or " "
        frame = slide.placeholders[1].text_frame
        items = [str(b) for b in bullets] if bullets else []
        if body:
            items.extend(line for line in body.split("\n") if line.strip())
        frame.clear()
        for index, item in enumerate(items):
            para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            para.text = item
            para.level = 0
            for run in para.runs:
                run.font.size = Pt(18)
                _set_font(run)
        for shape in slide.shapes:
            if shape.has_text_frame and shape != slide.placeholders[1]:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        _set_font(run)

    prs.save(str(path))
    return f"[ok] 已生成 PPT：{path.name}（{len(prs.slides)} 页）"


def _write_docx(path: Path, title: str, subtitle: str, sections: list[dict]) -> str:
    from docx import Document

    doc = Document()
    if title:
        doc.add_heading(title, level=0)
    if subtitle:
        doc.add_paragraph(subtitle)
    for section in sections:
        heading = str(section.get("heading") or "").strip()
        if heading:
            doc.add_heading(heading, level=1)
        body = str(section.get("body") or "").strip()
        if body:
            for line in body.split("\n"):
                if line.strip():
                    doc.add_paragraph(line.strip())
        for bullet in section.get("bullets") or []:
            doc.add_paragraph(str(bullet), style="List Bullet")
    doc.save(str(path))
    return f"[ok] 已生成 Word：{path.name}（{len(sections)} 节）"


def _write_xlsx(path: Path, title: str, sections: list[dict], rows: Any) -> str:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = (title or "Sheet1")[:30]

    data = rows if isinstance(rows, list) and rows else []
    if not data:
        for section in sections:
            heading = str(section.get("heading") or "")
            if heading:
                data.append([heading])
            for bullet in section.get("bullets") or []:
                data.append([str(bullet)])
            body = str(section.get("body") or "").strip()
            if body:
                data.append([body])
    for row in data:
        if isinstance(row, (list, tuple)):
            ws.append(list(row))
        else:
            ws.append([row])
    wb.save(str(path))
    return f"[ok] 已生成 Excel：{path.name}（{ws.max_row} 行）"


def office_write(args: dict[str, Any], root: Path) -> str:
    kind = str(args.get("kind", "")).strip().lower()
    if kind not in {"pptx", "docx", "xlsx"}:
        return "[error] kind 只能是 pptx / docx / xlsx"

    path, error = _resolve(root, str(args.get("path", "")))
    if path is None:
        return f"[error] {error}"
    if not path.suffix:
        path = path.with_suffix("." + kind)
    elif path.suffix.lower() != "." + kind:
        return f"[error] 路径后缀 {path.suffix} 和 kind={kind} 对不上"

    sections = _sections(args)
    title = str(args.get("title") or "").strip() or path.stem
    subtitle = str(args.get("subtitle") or "").strip()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "pptx":
            note = _write_pptx(path, title, subtitle, sections)
        elif kind == "docx":
            note = _write_docx(path, title, subtitle, sections)
        else:
            note = _write_xlsx(path, title, sections, args.get("rows"))
    except ImportError as exc:
        return f"[error] 缺少生成 {kind} 的库：{exc}"
    except Exception as exc:
        return f"[error] 生成失败：{type(exc).__name__}: {exc}"

    size = path.stat().st_size if path.exists() else 0
    return f"{note}，路径：{_rel(root, path)}（{size / 1024:.0f} KB）"


def register_office(registry: ToolRegistry, cfg=None) -> None:
    root = _root_of(cfg)

    registry.register(
        ToolSpec(
            name="office_write",
            description=(
                "直接生成 Office 文件（ppt/word/excel），返回文件路径。"
                "用户要“做一份 PPT/报告/表格”时必须用这个，"
                "不要把代码贴给用户让他们自己跑。"
                "sections 是内容：每项 {heading, bullets 或 body}；"
                "pptx 里一项=一页，docx 里一项=一节，xlsx 里一项=一组行。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["pptx", "docx", "xlsx"],
                        "description": "文件类型",
                    },
                    "path": {
                        "type": "string",
                        "description": "输出路径（相对工作目录），例如 docs/周报.pptx",
                    },
                    "title": {"type": "string", "description": "文档标题"},
                    "subtitle": {"type": "string", "description": "副标题（pptx 封面用）"},
                    "sections": {
                        "type": "array",
                        "description": "内容小节",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string", "description": "小节标题"},
                                "body": {"type": "string", "description": "正文，用换行分段"},
                                "bullets": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "要点列表",
                                },
                            },
                        },
                    },
                    "rows": {
                        "type": "array",
                        "description": "仅 xlsx 用：二维数组，每行一条记录",
                        "items": {"type": "array", "items": {}},
                    },
                },
                "required": ["kind", "path"],
            },
            handler=lambda args: office_write(args, root),
            risk="high",
        )
    )
