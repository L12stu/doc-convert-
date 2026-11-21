"""结构化 Markdown → DOCX 排版。

服务于扫描件重建链路：Markdown 只是中间载体，最终交付的是可编辑 Word。
样式保持克制——标题分级、正文宋体、行距 1.35，不追求还原原刊物版式。
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

SENT_END = re.compile(r"[。！？；：…\.\!\?\)）\"'”’¹²³⁴⁵⁶⁷⁸⁹⁰]$")
NO_INDENT = re.compile(r"^(?:[A-D]\)|W[:：]|M[:：]|【|\[\d+\]|\d+[\.、]\s*$)")
INLINE_BOLD = re.compile(r"(\*\*[^*]+\*\*)")


def _style_run(run, size: float, bold: bool = False, cn: str = "宋体", en: str = "Times New Roman") -> None:
    run.font.name = en
    run._element.rPr.rFonts.set(qn("w:eastAsia"), cn)
    run.font.size = Pt(size)
    run.bold = bold


def _add_inline(paragraph, text: str, size: float = 10.5) -> None:
    for segment in INLINE_BOLD.split(text):
        if not segment:
            continue
        if segment.startswith("**") and segment.endswith("**"):
            _style_run(paragraph.add_run(segment[2:-2]), size, bold=True)
        else:
            _style_run(paragraph.add_run(segment), size)


def _join_wrapped(blocks: list[str]) -> list[str]:
    """跨页/跨栏被切断的段落接回去；标题、引用、选项行不参与拼接。"""
    merged: list[str] = []
    for block in blocks:
        previous = merged[-1] if merged else ""
        skip = (
            not merged
            or len(previous) < 20
            or previous.startswith(("#", ">"))
            or re.match(r"^(?:[A-D]\)|【)", previous)
            or SENT_END.search(previous)
            or re.match(r"^(?:#|>|[A-D]\)|【|\*\*\d)", block)
        )
        if skip:
            merged.append(block)
        else:
            joiner = "" if re.match(r"[\u4e00-\u9fff]", previous[-1:]) or re.match(r"[\u4e00-\u9fff]", block[:1]) else " "
            merged[-1] = previous + joiner + block
    return merged


def markdown_to_docx(md_path, docx_path, title: str | None = None, join_wrapped: bool = True) -> Path:
    md_path = Path(md_path)
    docx_path = Path(docx_path)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", md_path.read_text(encoding="utf-8")) if b.strip()]
    if join_wrapped:
        blocks = _join_wrapped(blocks)

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.2)
    section.top_margin = section.bottom_margin = Cm(2.2)

    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    normal.paragraph_format.line_spacing = 1.35
    normal.paragraph_format.space_after = Pt(6)

    if title:
        heading = document.add_paragraph()
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        heading.paragraph_format.space_after = Pt(14)
        _style_run(heading.add_run(title), 18, bold=True, cn="微软雅黑")

    for block in blocks:
        if block.startswith(">"):
            body = "\n".join(line.lstrip("> ").rstrip() for line in block.splitlines())
            for line in body.splitlines():
                if not line.strip():
                    continue
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.6)
                paragraph.paragraph_format.space_after = Pt(2)
                paragraph.paragraph_format.line_spacing = 1.2
                _add_inline(paragraph, line.strip(), size=9)
                for run in paragraph.runs:
                    run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
            continue

        matched = re.match(r"^(#{1,5})\s+(.*)$", block)
        if matched:
            level, text = len(matched.group(1)), matched.group(2).strip()
            paragraph = document.add_paragraph()
            fmt = paragraph.paragraph_format
            if level <= 1:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                fmt.space_before, fmt.space_after = Pt(0), Pt(14)
                size = 17
            elif level == 2:
                fmt.space_before, fmt.space_after = Pt(16), Pt(8)
                size = 15
            elif level == 3:
                fmt.space_before, fmt.space_after = Pt(11), Pt(5)
                size = 12.5
            else:
                fmt.space_before, fmt.space_after = Pt(9), Pt(4)
                size = 11
            _style_run(paragraph.add_run(text), size, bold=True, cn="微软雅黑")
            continue

        paragraph = document.add_paragraph()
        if not NO_INDENT.match(block):
            paragraph.paragraph_format.first_line_indent = Cm(0.74)
        _add_inline(paragraph, block)

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(docx_path))
    return docx_path
