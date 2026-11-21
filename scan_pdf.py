"""扫描件 PDF 识别：渲染 → 系统 OCR → 版面重组 → Markdown + 质量指标。

只处理"没有文字层、整页是图片"的 PDF。有文字层的 PDF 应走引擎原生转换，
不要在这里绕路——OCR 的精度永远低于原文。

本模块的产出必须经过人工校对才能当作定稿：它解决的是"从零拿到可编辑文本"，
不是"无损还原原版面"。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import subprocess
from pathlib import Path

CJK = r"\u4e00-\u9fff"
CJK_RE = re.compile(f"[{CJK}]")
CN_PUNCT = "，。、；：？！“”‘’（）《》【】·…—"
SENT_END = re.compile(r"[。！？；：…\.\!\?\)）\"'”’]")
HEADING_PREFIX = re.compile(r"^(Part|Section|Unit|Chapter)\s+[IVXLC\d]+", re.I)
FOOTER_PAT = re.compile(
    r"(真\s*题|解\s*析|第\s*[一二三四]\s*套|六\s*[级绒]|考试|答案|解析|20\d\d\s*年)", re.I
)
PAGE_NUM_PAT = re.compile(r"^[\dOoIl\s\-–—·．.]{1,12}$")


class ScanError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 渲染与识别


def render_pages(pdf_path: Path, out_dir: Path, dpi: int = 300) -> list[dict]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - 运行环境自带
        raise ScanError("SCAN_UNSUPPORTED", "缺少 PDF 渲染组件") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    try:
        if doc.page_count == 0:
            raise ScanError("SCAN_EMPTY", "PDF 没有任何页面")
        pages = []
        for index in range(doc.page_count):
            pixmap = doc[index].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
            target = out_dir / f"page_{index + 1:04d}.png"
            pixmap.save(str(target))
            pages.append({"path": target, "width": pixmap.width, "height": pixmap.height})
        return pages
    finally:
        doc.close()


def run_ocr(page_dir: Path, json_path: Path, language: str, timeout: int = 900) -> list[dict]:
    if os.name != "nt":
        raise ScanError("SCAN_UNSUPPORTED", "扫描件识别依赖 Windows 内置 OCR，当前系统不支持")
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise ScanError("SCAN_UNSUPPORTED", "找不到 PowerShell，无法调用系统 OCR")

    script = Path(__file__).with_name("win_ocr.ps1")
    if not script.is_file():
        raise ScanError("SCAN_UNSUPPORTED", "skill 缺少 OCR 调用脚本，安装不完整")

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Dir",
            str(page_dir),
            "-OutJson",
            str(json_path),
            "-Language",
            language,
        ],
        capture_output=True,
        text=True,
        errors="ignore",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0 or not json_path.is_file():
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise ScanError("SCAN_FAILED", f"系统 OCR 调用失败：{detail[-1] if detail else '无输出'}")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    pages = payload.get("pages") or []
    if isinstance(pages, dict):
        pages = [pages]
    return pages


# ---------------------------------------------------------------- 文本清洗


def collapse_cjk_spaces(text: str) -> str:
    """OCR 常把中文逐字分隔，这里把汉字与汉字、汉字与中文标点之间的空格去掉。"""
    pattern = f"(?<=[{CJK}{CN_PUNCT}])\\s+(?=[{CJK}{CN_PUNCT}])"
    previous = None
    while previous != text:
        previous = text
        text = re.sub(pattern, "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def latinise_punctuation(text: str) -> str:
    """纯英文行里的全角标点还原成半角，避免英文句子夹中文逗号。"""
    if not text:
        return text
    cjk = len(CJK_RE.findall(text))
    if cjk > len(text) * 0.1:
        return text
    text = re.sub(r"\s*，\s*", ", ", text)
    text = re.sub(r"\s*。\s*", ". ", text)
    text = re.sub(r"\s*；\s*", "; ", text)
    text = re.sub(r"\s*：\s*", ": ", text)
    text = re.sub(r"\s*、\s*", ", ", text)
    # OCR 常把英文撇号识别成逗号：country , s -> country's
    text = re.sub(r"(?<=[A-Za-z])\s*,\s*(?=(?:s|t|re|ve|ll|m|d)\b)", "'", text)
    text = text.replace("（", " (").replace("）", ") ").replace("“", '"').replace("”", '"')
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def is_margin_line(line: dict, page_h: float) -> bool:
    """剔除页眉页脚。只认强特征，避免误删正文里的 Part / Section 标题。"""
    y = line["y"]
    text = line["text"].strip()
    if not text:
        return True
    edge = y < page_h * 0.055 or y > page_h * 0.935
    if not edge:
        return False
    if HEADING_PREFIX.match(text):
        return False
    return bool(FOOTER_PAT.search(text) or PAGE_NUM_PAT.match(text))


# ---------------------------------------------------------------- 版面重组


def detect_two_column(lines: list[dict], page_w: float) -> bool:
    usable = [L for L in lines if L["w"] > page_w * 0.12]
    if len(usable) < 8:
        return False
    widths = [L["w"] for L in usable]
    if statistics.median(widths) > page_w * 0.5:
        return False
    left = sum(1 for L in usable if L["x"] < page_w * 0.45)
    right = sum(1 for L in usable if L["x"] >= page_w * 0.45)
    return left > len(usable) * 0.25 and right > len(usable) * 0.25


def split_side_notes(lines: list[dict], page_w: float) -> tuple[list[dict], list[dict]]:
    """单栏版式里，右侧窄列通常是旁注/批注，单独取出来附在页尾。"""
    body, side = [], []
    for line in lines:
        narrow_right = line["x"] >= page_w * 0.60 and line["w"] <= page_w * 0.32
        (side if narrow_right else body).append(line)
    return body, side


def merge_visual_rows(lines: list[dict], line_h: float) -> list[dict]:
    """同一水平线上的左右两段（如选项 A/B 与 C/D）合并成一行，按 x 排序拼接。"""
    ordered = sorted(lines, key=lambda L: (L["y"], L["x"]))
    rows: list[dict] = []
    tolerance = max(10.0, line_h * 0.55)
    for line in ordered:
        if rows:
            last = rows[-1]
            if abs(line["y"] - last["y"]) <= tolerance:
                parts = last.setdefault("parts", [(last["x"], last["text"])])
                parts.append((line["x"], line["text"]))
                parts.sort(key=lambda item: item[0])
                last["text"] = "    ".join(text for _, text in parts)
                last["x"] = min(last["x"], line["x"])
                last["x1"] = max(last.get("x1", last["x"] + last["w"]), line["x"] + line["w"])
                continue
        rows.append(
            {
                "text": line["text"],
                "x": line["x"],
                "y": line["y"],
                "w": line["w"],
                "h": line["h"],
                "x1": line["x"] + line["w"],
            }
        )
    return rows


def group_paragraphs(rows: list[dict], line_h: float) -> list[dict]:
    """按行距与段首缩进切段，行末无句末标点则视为同段续行。"""
    paragraphs: list[dict] = []
    previous: dict | None = None
    for row in rows:
        text = latinise_punctuation(collapse_cjk_spaces(row["text"]))
        if not text:
            continue
        starts_new = True
        if previous is not None:
            gap = row["y"] - previous["y"]
            indent = row["x"] - previous["x"]
            same_flow = gap <= line_h * 1.75 and indent < line_h * 1.3
            unfinished = not SENT_END.search(previous["text"].rstrip()[-1:] or " ")
            speaker = re.match(r"^[WM]\s*[:：．.]", text) and len(text) > 3
            if same_flow and (unfinished or indent < line_h * 0.6) and not speaker:
                starts_new = False
        if starts_new:
            paragraphs.append({"text": text, "h": row["h"], "x": row["x"]})
        else:
            joiner = "" if CJK_RE.match(paragraphs[-1]["text"][-1:]) or CJK_RE.match(text[:1]) else " "
            paragraphs[-1]["text"] = paragraphs[-1]["text"] + joiner + text
            paragraphs[-1]["h"] = max(paragraphs[-1]["h"], row["h"])
        previous = row
    return paragraphs


def build_markdown(pages: list[dict], ocr_pages: list[dict], pdf_name: str) -> tuple[str, dict]:
    blocks: list[str] = [f"# {Path(pdf_name).stem}", ""]
    stats = {"pages": 0, "paragraphs": 0, "chars": 0, "side_notes": 0, "two_column_pages": 0}

    for index, ocr_page in enumerate(ocr_pages):
        width = float(ocr_page.get("width") or 1)
        height = float(ocr_page.get("height") or 1)
        raw_lines = [L for L in (ocr_page.get("lines") or []) if str(L.get("text", "")).strip()]
        lines = [L for L in raw_lines if not is_margin_line(L, height)]
        if not lines:
            continue

        heights = [L["h"] for L in lines if L["h"] > 10]
        line_h = statistics.median(heights) if heights else 40.0

        side: list[dict] = []
        if detect_two_column(lines, width):
            stats["two_column_pages"] += 1
            left = [L for L in lines if L["x"] < width * 0.48]
            right = [L for L in lines if L["x"] >= width * 0.48]
            body = merge_visual_rows(left, line_h) + merge_visual_rows(right, line_h)
        else:
            body_lines, side = split_side_notes(lines, width)
            body = merge_visual_rows(body_lines, line_h)

        paragraphs = group_paragraphs(body, line_h)
        if not paragraphs:
            continue

        stats["pages"] += 1
        stats["chars"] += sum(len(p["text"]) for p in paragraphs)
        stats["side_notes"] += len(side)

        for para in paragraphs:
            text = para["text"]
            is_heading = (
                para["h"] >= line_h * 1.28
                and len(text) <= 40
                and not SENT_END.search(text[-1:] or " ")
            )
            blocks.append(("### " + text) if is_heading else text)
            blocks.append("")
            stats["paragraphs"] += 1

        if side:
            joined = " ".join(L["text"] for L in sorted(side, key=lambda L: L["y"]))
            blocks.append(f"> 【旁注】{collapse_cjk_spaces(joined)}")
            blocks.append("")

    return "\n".join(blocks).rstrip() + "\n", stats


# ---------------------------------------------------------------- 质量闸门

SUSPECT_TOKEN = re.compile(r"(?i)\b(?:t0|tO|0f|0n|0r|0ne|1n|1t|1s|l0|0ff|rn)\b|[A-Za-z]{2,}\d|\d[A-Za-z]{2,}")


def assess_quality(markdown: str, stats: dict) -> dict:
    """给出可核对的量化指标，用于在交付前判断"能不能直接用"。"""
    latin_tokens = re.findall(r"[A-Za-z][A-Za-z0-9']*", markdown)
    suspect = [t for t in latin_tokens if SUSPECT_TOKEN.search(t)]
    latin_ratio = round(len(suspect) / len(latin_tokens), 4) if latin_tokens else 0.0

    cjk_gaps = sum(
        len(re.findall(f"(?<=[{CJK}])[ \\t\\u3000]+(?=[{CJK}])", line)) for line in markdown.splitlines()
    )
    per_page = round(stats["chars"] / stats["pages"], 1) if stats["pages"] else 0.0

    warnings: list[str] = []
    if per_page < 80:
        warnings.append("识别到的文字量异常偏少，可能存在图片过淡或版式异常，请检查原文件是否清晰")
    if latin_ratio > 0.02:
        warnings.append("英文识别存在可察觉的误差（数字/字母混淆），英文部分需逐段校对")
    if cjk_gaps > 0:
        warnings.append("中文文本仍有未清理的字间空格")

    return {
        "chars_per_page": per_page,
        "latin_suspect_ratio": latin_ratio,
        "cjk_gap_count": cjk_gaps,
        "pages": stats["pages"],
        "paragraphs": stats["paragraphs"],
        "two_column_pages": stats["two_column_pages"],
        "side_note_blocks": stats["side_notes"],
        "needs_review": True,
        "warnings": warnings,
    }


# ---------------------------------------------------------------- 对外入口


def scan_pdf(pdf_path: Path, work_dir: Path, dpi: int = 300, language: str = "zh-Hans-CN") -> dict:
    """把扫描件 PDF 转成 Markdown，返回产物路径与质量指标。"""
    pdf_path = Path(pdf_path).expanduser().resolve()
    if not pdf_path.is_file():
        raise ScanError("INPUT_NOT_FOUND", "输入文件不存在")

    work_dir = Path(work_dir)
    page_dir = work_dir / "pages"
    pages = render_pages(pdf_path, page_dir, dpi=dpi)

    json_path = work_dir / "ocr.json"
    ocr_pages = run_ocr(page_dir, json_path, language)

    markdown, stats = build_markdown(pages, ocr_pages, pdf_path.name)
    if not stats["pages"]:
        raise ScanError("SCAN_EMPTY", "整份文件没有识别出可用文字，可能是空白页或图片质量过低")

    md_path = work_dir / "document.md"
    md_path.write_text(markdown, encoding="utf-8")

    quality = assess_quality(markdown, stats)
    warnings = [
        "这份 PDF 是扫描件，没有文字层，内容由本机 OCR 识别重建，个别字符（尤其英文与专有名词）可能有误，交付前需校对",
        *quality["warnings"],
    ]
    return {
        "markdown": md_path,
        "pages": len(pages),
        "dpi": dpi,
        "language": language,
        "quality": quality,
        "warnings": warnings,
    }


def markdown_to_plain(markdown: str) -> str:
    """去掉 markdown 标记，供 txt 输出使用。"""
    lines = []
    for line in markdown.splitlines():
        if line.startswith("#"):
            lines.append(line.lstrip("# ").strip())
        elif line.startswith(">"):
            lines.append(line.lstrip("> ").strip())
        else:
            lines.append(line.replace("**", ""))
    return "\n".join(lines)


def markdown_to_html(markdown: str, title: str) -> str:
    escaped = markdown.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = []
    for line in escaped.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("### "):
            body.append(f"<h3>{text[4:]}</h3>")
        elif text.startswith("## "):
            body.append(f"<h2>{text[3:]}</h2>")
        elif text.startswith("# "):
            body.append(f"<h1>{text[2:]}</h1>")
        elif text.startswith("&gt; "):
            body.append(f"<blockquote>{text[5:]}</blockquote>")
        else:
            body.append(f"<p>{text}</p>")
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title>"
        "<style>body{font-family:'Songti SC','SimSun',serif;line-height:1.8;max-width:820px;"
        "margin:40px auto;padding:0 20px;color:#222}h1,h2,h3{font-family:'Microsoft YaHei',sans-serif}"
        "blockquote{color:#555;border-left:3px solid #ddd;margin:8px 0;padding-left:12px;font-size:.92em}"
        "</style></head><body>" + "\n".join(body) + "</body></html>"
    )
