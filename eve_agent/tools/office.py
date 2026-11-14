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


def _write_pptx(
    path: Path,
    title: str,
    subtitle: str,
    sections: list[dict],
    append: bool = False,
) -> str:
    from pptx import Presentation
    from pptx.util import Pt

    resumed = append and path.exists()
    prs = Presentation(str(path)) if resumed else Presentation()

    if not resumed:
        cover = prs.slides.add_slide(prs.slide_layouts[0])
        cover.shapes.title.text = title or "未命名"
        if len(cover.placeholders) > 1:
            cover.placeholders[1].text = subtitle
        for shape in cover.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        _set_font(run)

    added = 0
    for section in sections:
        heading = str(section.get("heading") or "").strip()
        bullets = section.get("bullets") or []
        body = str(section.get("body") or "").strip()
        if not heading and not bullets and not body:
            continue

        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = heading or " "
        frame = slide.placeholders[1].text_frame

        # 要点可以写成字符串，也可以写成 {"text": ..., "level": 1} 表示缩进一级
        items: list[tuple[str, int]] = []
        for bullet in bullets:
            if isinstance(bullet, dict):
                text = str(bullet.get("text") or "").strip()
                level = 1 if int(bullet.get("level") or 0) else 0
            else:
                text, level = str(bullet).strip(), 0
            if text:
                items.append((text, level))
        if body:
            items.extend((line.strip(), 0) for line in body.split("\n") if line.strip())

        # 字少就放大一点，字多就缩小，避免一页塞满看不清
        count = len(items)
        size = 22 if count <= 3 else 20 if count <= 5 else 18 if count <= 7 else 15

        frame.clear()
        for index, (item, level) in enumerate(items):
            para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            para.text = item
            para.level = level
            try:
                para.line_spacing = 1.2
            except Exception:
                pass
            for run in para.runs:
                run.font.size = Pt(size if level == 0 else size - 2)
                _set_font(run)

        notes = str(section.get("notes") or "").strip()
        if notes:
            try:
                # 备注页是懒创建的：直接取 notes_slide 就会建出来（has_notes_slide 此时还是 False）
                slide.notes_slide.notes_text_frame.text = notes
            except Exception:
                pass

        for shape in slide.shapes:
            if shape.has_text_frame and shape != slide.placeholders[1]:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        _set_font(run)
        added += 1

    if not added and not resumed:
        # 允许"只有封面"的文件（用户可能只想要个壳），但要说清楚，别让模型以为内容写进去了
        note = "（没有正文页：sections 是空的，如果你本来要写内容，说明参数没传对）"
    else:
        note = ""

    prs.save(str(path))
    verb = "追加" if resumed else "已生成"
    return (
        f"[ok] {verb} PPT：{path.name}（共 {len(prs.slides)} 页，本次 {added} 页）{note}"
    )


def _write_docx(
    path: Path,
    title: str,
    subtitle: str,
    sections: list[dict],
    append: bool = False,
) -> str:
    from docx import Document

    resumed = append and path.exists()
    doc = Document(str(path)) if resumed else Document()

    if not resumed:
        if title:
            doc.add_heading(title, level=0)
        if subtitle:
            doc.add_paragraph(subtitle)

    added = 0
    for section in sections:
        heading = str(section.get("heading") or "").strip()
        if heading:
            # level 1–3，默认 1；文章的小节一般用 1，子标题用 2
            try:
                level = max(1, min(3, int(section.get("level") or 1)))
            except (TypeError, ValueError):
                level = 1
            doc.add_heading(heading, level=level)
        body = str(section.get("body") or "").strip()
        if body:
            for line in body.split("\n"):
                if line.strip():
                    doc.add_paragraph(line.strip())
        for bullet in section.get("bullets") or []:
            if isinstance(bullet, dict):
                text = str(bullet.get("text") or "").strip()
                deep = bool(bullet.get("level"))
            else:
                text, deep = str(bullet).strip(), False
            if text:
                style = "List Bullet 2" if deep else "List Bullet"
                try:
                    doc.add_paragraph(text, style=style)
                except KeyError:  # 模板里没有二级列表样式时退回一级
                    doc.add_paragraph(text, style="List Bullet")
        if heading or body or section.get("bullets"):
            added += 1

    if not added and not resumed:
        note = "（没有正文：sections 是空的，如果你本来要写内容，说明参数没传对）"
    else:
        note = ""

    doc.save(str(path))
    verb = "追加" if resumed else "已生成"
    return f"[ok] {verb} Word：{path.name}（本次 {added} 节）{note}"


def _write_xlsx(
    path: Path, title: str, sections: list[dict], rows: Any, append: bool = False
) -> str:
    from openpyxl import Workbook, load_workbook

    resumed = append and path.exists()
    if resumed:
        wb = load_workbook(str(path))
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = (title or "Sheet1")[:30]

    start_row = ws.max_row
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
    verb = "追加" if resumed else "已生成"
    return f"[ok] {verb} Excel：{path.name}（共 {ws.max_row} 行，本次 {ws.max_row - start_row} 行）"


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
    append = bool(args.get("append"))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "pptx":
            note = _write_pptx(path, title, subtitle, sections, append)
        elif kind == "docx":
            note = _write_docx(path, title, subtitle, sections, append)
        else:
            note = _write_xlsx(path, title, sections, args.get("rows"), append)
    except ImportError as exc:
        return f"[error] 缺少生成 {kind} 的库：{exc}"
    except Exception as exc:
        return f"[error] 生成失败：{type(exc).__name__}: {exc}"

    if note.startswith("[error]"):
        return note
    size = path.stat().st_size if path.exists() else 0
    return f"{note}，路径：{_rel(root, path)}（{size / 1024:.0f} KB）"


def register_office(registry: ToolRegistry, cfg=None) -> None:
    root = _root_of(cfg)

    registry.register(
        ToolSpec(
            name="office_write",
            description=(
                "直接生成或追加 Office 文件（ppt/word/excel），返回文件路径。"
                "用户要“做一份 PPT / 写篇报告 / 整理成表格”时必须用这个，"
                "不要把代码贴给用户让他们自己跑。"
                "sections 是内容：每项 {heading, bullets 或 body, level, notes}；"
                "pptx 里一项=一页（notes 是讲稿），docx 里一项=一节（level 是标题级别 1–3），"
                "xlsx 里用 rows 给二维数组。"
                "内容长的时候分几次写：第一次不带 append 建文件，之后带 append=true 继续加章节。"
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
                    "append": {
                        "type": "boolean",
                        "description": "true = 在已有文件后面追加章节（长文档分块写时用）",
                    },
                    "sections": {
                        "type": "array",
                        "description": "内容小节",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string", "description": "小节标题"},
                                "body": {"type": "string", "description": "正文，用换行分段"},
                                "level": {
                                    "type": "integer",
                                    "description": "docx 标题级别 1–3，默认 1",
                                },
                                "notes": {
                                    "type": "string",
                                    "description": "pptx 讲稿（放备注里，不上屏）",
                                },
                                "bullets": {
                                    "type": "array",
                                    "description": "要点列表",
                                    "items": {
                                        "anyOf": [
                                            {"type": "string"},
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "text": {"type": "string"},
                                                    "level": {
                                                        "type": "integer",
                                                        "description": "缩进级别，0 或 1",
                                                    },
                                                },
                                                "required": ["text"],
                                            },
                                        ]
                                    },
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
