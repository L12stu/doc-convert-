#!/usr/bin/env python3
"""doc-convert skill 入口脚本。

用任意 Python 3.9+ 执行即可。首次运行会在独立虚拟环境中安装转换引擎，
之后自动复用该环境，不会污染调用方的 Python 环境，也不要求项目源码存在。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS = SKILL_ROOT / "assets"
DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
OFFICIAL_INDEX = "https://pypi.org/simple"


def log(message: str) -> None:
    print(f"[doc-convert] {message}", file=sys.stderr, flush=True)


def state_home() -> Path:
    """运行环境存放目录，默认在用户缓存区，避免污染 skill 目录本身。"""
    custom = os.environ.get("DOCCONVERT_SKILL_HOME")
    if custom:
        return Path(custom).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "doc-convert-skill"


def env_python(home: Path) -> Path:
    if os.name == "nt":
        return home / "venv" / "Scripts" / "python.exe"
    return home / "venv" / "bin" / "python"


def same_path(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return False


def engine_ready(python: Path) -> bool:
    if not python.exists():
        return False
    probe = subprocess.run(
        [str(python), "-c", "import docconvert"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


def _pip_install(python: Path, index: str, targets: list[str], no_deps: bool = False) -> int:
    """在目标环境里执行 pip install。

    引擎包用 --no-deps 安装：它的元数据钉住了整套服务端依赖（FastAPI/Redis/RQ/S3），
    本 skill 只用本地转换那部分，多余组件由 requirements.txt 精确控制。
    """
    args = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--index-url",
        index,
    ]
    if no_deps:
        args.append("--no-deps")
    args.extend(targets)
    return subprocess.run(args, check=False).returncode


def bootstrap(home: Path) -> Path:
    """创建独立虚拟环境并安装转换引擎，返回其解释器路径。"""
    wheels = sorted(ASSETS.glob("docconvert-*.whl"))
    if not wheels:
        raise SystemExit("[doc-convert] assets/ 中缺少 docconvert 安装包，skill 不完整")
    requirements = ASSETS / "requirements.txt"
    if not requirements.exists():
        raise SystemExit("[doc-convert] assets/ 中缺少 requirements.txt，skill 不完整")

    home.mkdir(parents=True, exist_ok=True)
    python = env_python(home)
    if not python.exists():
        log(f"首次使用，正在创建运行环境：{home}")
        result = subprocess.run([sys.executable, "-m", "venv", str(home / "venv")], check=False)
        if result.returncode != 0 or not python.exists():
            raise SystemExit("[doc-convert] 虚拟环境创建失败，请确认当前 Python 带有 venv 模块")

    index = os.environ.get("DOCCONVERT_PIP_INDEX") or DEFAULT_INDEX

    def install(index_url: str) -> int:
        code = _pip_install(python, index_url, [str(wheels[-1])], no_deps=True)
        if code != 0:
            return code
        return _pip_install(python, index_url, ["-r", str(requirements)])

    log("正在安装转换引擎依赖（首次约 150 MB，耗时取决于网络）")
    code = install(index)
    if code != 0 and index != OFFICIAL_INDEX:
        log("镜像源安装失败，改用官方 PyPI 重试")
        index = OFFICIAL_INDEX
        code = install(OFFICIAL_INDEX)
    if code != 0 or not engine_ready(python):
        raise SystemExit("[doc-convert] 依赖安装失败，请检查网络后重试")
    log("运行环境就绪")
    return python


def ensure_engine() -> Path:
    home = state_home()
    python = env_python(home)
    if engine_ready(python):
        return python
    return bootstrap(home)


def reexec_if_needed(python: Path) -> None:
    """当前解释器不是运行环境时，切换过去重新执行同一命令。"""
    if same_path(Path(sys.executable), python):
        return
    completed = subprocess.run([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], check=False)
    sys.exit(completed.returncode)


def _find_bundled_libreoffice(home: Path) -> Path | None:
    """在 skill 私有目录里找已解包的 LibreOffice，避免依赖用户手动安装。"""
    root = home / "libreoffice"
    if not root.is_dir():
        return None
    name = "soffice.exe" if os.name == "nt" else "soffice"
    for candidate in root.rglob(name):
        if candidate.is_file():
            return candidate
    return None


def ensure_libreoffice() -> Path | None:
    """让引擎能找到 LibreOffice：显式配置 > 系统安装 > skill 自带解包副本。"""
    configured = os.environ.get("DOCCONVERT_LIBREOFFICE_PATH")
    if configured and Path(configured).exists():
        return Path(configured)
    system = shutil.which("soffice") or shutil.which("libreoffice")
    if system:
        os.environ["DOCCONVERT_LIBREOFFICE_PATH"] = system
        return Path(system)
    bundled = _find_bundled_libreoffice(state_home())
    if bundled:
        os.environ["DOCCONVERT_LIBREOFFICE_PATH"] = str(bundled)
        return bundled
    return None


def typst_binary() -> Path | None:
    """skill 自带的 Typst 引擎，用于没有 LibreOffice 时输出 PDF。"""
    name = "typst.exe" if os.name == "nt" else "typst"
    root = state_home() / "bin"
    if not root.is_dir():
        return None
    direct = root / name
    if direct.is_file():
        return direct
    for candidate in root.rglob(name):
        if candidate.is_file():
            return candidate
    found = shutil.which("typst")
    return Path(found) if found else None


TYPST_ASSETS = {
    "win32": ("typst-x86_64-pc-windows-msvc.zip", "zip"),
    "darwin": ("typst-x86_64-apple-darwin.tar.xz", "tar"),
    "linux": ("typst-x86_64-unknown-linux-musl.tar.xz", "tar"),
}


def ensure_typst() -> Path | None:
    """没有 LibreOffice 时用的轻量 PDF 引擎；缺失则自动补齐，用户无需手动安装。"""
    existing = typst_binary()
    if existing:
        return existing

    asset = TYPST_ASSETS.get(sys.platform)
    if asset is None:
        return None

    import urllib.request

    filename, kind = asset
    root = state_home() / "bin"
    root.mkdir(parents=True, exist_ok=True)
    archive = root / filename
    url = f"https://github.com/typst/typst/releases/latest/download/{filename}"
    log("首次输出 PDF，正在准备内置排版引擎（约 20 MB）")

    try:
        if not archive.is_file() or archive.stat().st_size < 5 * 1024 * 1024:
            with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as handle:
                shutil.copyfileobj(response, handle, 256 * 1024)
        if kind == "zip":
            with zipfile.ZipFile(archive) as package:
                package.extractall(root)
        else:
            import tarfile

            with tarfile.open(archive) as package:
                package.extractall(root)
    except Exception as exc:  # noqa: BLE001 - 下载失败要降级成可读错误
        log(f"排版引擎准备失败：{exc}")
        return None

    name = "typst.exe" if os.name == "nt" else "typst"
    for candidate in root.rglob(name):
        if candidate.is_file():
            if candidate.parent != root:
                shutil.copyfile(candidate, root / name)
                return root / name
            return candidate
    return None


def _cjk_font() -> str | None:
    """Typst 排版需要的字体名，按平台给默认中文字体。"""
    if sys.platform == "win32":
        return "Microsoft YaHei"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


def pdf_via_typst(source: Path, source_format, workspace: Path, engine) -> Path:
    """无 LibreOffice 时输出 PDF：先归一化为 DOCX，再由 pandoc + Typst 重新排版。"""
    from docconvert.errors import ConversionError
    from docconvert.formats import Format

    typst = ensure_typst()
    if typst is None:
        raise ConversionError(
            "LIBREOFFICE_UNAVAILABLE", "未安装 LibreOffice，也无法准备内置 PDF 排版引擎", 503, True
        )

    import pypandoc

    if source_format == Format.DOCX:
        docx_path = source
    else:
        (workspace / "pdf-source").mkdir(parents=True, exist_ok=True)
        docx_path = engine.convert(source, source_format, Format.DOCX, workspace / "pdf-source").path

    pandoc = pypandoc.get_pandoc_path()
    typ_source = workspace / "result.typ"
    rendered = subprocess.run(
        [
            pandoc,
            str(docx_path),
            "-t",
            "typst",
            "-o",
            str(typ_source),
            "--extract-media=media",
        ],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        errors="ignore",
    )
    if rendered.returncode != 0 or not typ_source.is_file():
        detail = (rendered.stderr or "").strip().splitlines()
        raise ConversionError(
            "PDF_ENGINE_FAILED", f"PDF 生成失败：{detail[-1] if detail else '无法生成排版源'}", 500, False
        )

    # Typst 不接受 Windows 绝对路径引用图片，pandoc 相对抽取后再由 Typst 编译。
    font = _cjk_font()
    content = typ_source.read_text(encoding="utf-8")
    prefix = f'#set text(font: ("{font}",), lang: "zh")\n' if font else ""
    typ_source.write_text(prefix + content, encoding="utf-8")

    output = workspace / "result.pdf"
    compiled = subprocess.run(
        [str(typst), "compile", str(typ_source), str(output)],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        errors="ignore",
    )
    if compiled.returncode != 0 or not output.is_file():
        detail = (compiled.stderr or "").strip().splitlines()
        raise ConversionError(
            "PDF_ENGINE_FAILED", f"PDF 生成失败：{detail[-1] if detail else '排版引擎未产出文件'}", 500, False
        )
    return output


def supported_formats() -> dict:
    from docconvert.config import get_settings
    from docconvert.formats import MEDIA_TYPES, Format

    settings = get_settings()
    return {
        "formats": [
            {"name": fmt.value, "media_type": MEDIA_TYPES[fmt]} for fmt in sorted(Format, key=lambda f: f.value)
        ],
        "limits": {
            "max_file_size_bytes": settings.max_file_size,
            "max_pdf_pages": settings.max_pdf_pages,
            "max_output_size_bytes": settings.max_output_size,
            "conversion_timeout_seconds": settings.conversion_timeout,
        },
        "scan": {
            "available": os.name == "nt",
            "targets": sorted(SCAN_TARGETS),
            "note": "仅用于没有文字层的扫描件 PDF；结果由 OCR 重建，必须人工校对后才能当作定稿，不代表原版式还原",
        },
    }


SCAN_TARGETS = {"docx", "md", "txt", "html"}
SCAN_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown",
    "txt": "text/plain",
    "html": "text/html",
}


def scan_convert(source: Path, target: str, workspace: Path) -> tuple[Path, str, list[str], dict]:
    """扫描件（无文字层）专用：OCR 重建后按目标格式落盘，并带出质量报告。"""
    from md_to_docx import markdown_to_docx
    from scan_pdf import ScanError, markdown_to_html, markdown_to_plain, scan_pdf

    from docconvert.errors import ConversionError

    try:
        data = scan_pdf(source, workspace / "scan")
    except ScanError as exc:
        raise ConversionError(exc.code, exc.message, 422, False) from exc

    markdown_path = data["markdown"]
    markdown = markdown_path.read_text(encoding="utf-8")
    if target == "docx":
        result_path = markdown_to_docx(markdown_path, workspace / "scan" / "result.docx")
    elif target == "txt":
        result_path = workspace / "scan" / "result.txt"
        result_path.write_text(markdown_to_plain(markdown), encoding="utf-8")
    elif target == "html":
        result_path = workspace / "scan" / "result.html"
        result_path.write_text(markdown_to_html(markdown, source.stem), encoding="utf-8")
    else:
        result_path = markdown_path

    meta = {
        "engine": "windows-ocr",
        "pages": data["pages"],
        "dpi": data["dpi"],
        "needs_review": True,
        "quality": data["quality"],
    }
    return result_path, SCAN_MEDIA[target], list(data["warnings"]), meta


def convert_one(
    source: Path,
    target_text: str,
    output_arg: Path | None,
    unpack: bool,
    overwrite: bool,
    allow_scan: bool = True,
) -> dict:
    from docconvert.config import get_settings
    from docconvert.converter import ConversionEngine
    from docconvert.errors import ConversionError
    from docconvert.formats import Format, detect_format, normalize_format
    from docconvert.security import validate_upload

    settings = get_settings()
    target = normalize_format(target_text)
    source = source.expanduser().resolve()
    if not source.is_file():
        return {"input": str(source), "ok": False, "error": {"code": "INPUT_NOT_FOUND", "message": "输入文件不存在"}}

    try:
        source_format = detect_format(source, source.name)
        if source_format == target:
            raise ConversionError("SAME_FORMAT", "输入与输出格式相同，无需转换", 400)
        validate_upload(source, source_format, settings)

        with tempfile.TemporaryDirectory(prefix="docconvert-skill-") as temporary:
            engine = ConversionEngine(settings)
            scan_meta = None
            try:
                result = engine.convert(source, source_format, target, Path(temporary))
                result_path = result.path
                media_type = result.media_type
                warnings = list(result.warnings)
            except ConversionError as exc:
                if (
                    exc.code == "OCR_REQUIRED"
                    and allow_scan
                    and source_format == Format.PDF
                    and target.value in SCAN_TARGETS
                ):
                    log("检测到扫描件，改用 OCR 重建")
                    result_path, media_type, warnings, scan_meta = scan_convert(source, target.value, Path(temporary))
                elif exc.code != "LIBREOFFICE_UNAVAILABLE" or target.value != "pdf":
                    raise
                else:
                    result_path = pdf_via_typst(source, source_format, Path(temporary), engine)
                    media_type = "application/pdf"
                    warnings = ["未安装 LibreOffice，PDF 由内置 Typst 引擎重新排版生成，非原版式还原"]
            suffix = result_path.suffix.lower()

            if output_arg is None:
                destination = source.with_name(f"{source.stem}{suffix}")
            elif output_arg.is_dir() or str(output_arg).endswith(("/", "\\")):
                destination = output_arg / f"{source.stem}{suffix}"
            elif output_arg.suffix.lower() == suffix:
                destination = output_arg
            else:
                destination = output_arg.with_suffix(suffix)

            if same_path(destination, source) and suffix == source.suffix.lower():
                raise ConversionError("OUTPUT_OVERWRITE", "输出会覆盖输入文件，请指定其他路径", 400)
            if destination.exists() and not overwrite:
                raise ConversionError("OUTPUT_EXISTS", f"输出已存在：{destination}", 409)

            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(result_path, destination)

            payload = {
                "input": str(source),
                "ok": True,
                "target": target.value,
                "output": str(destination),
                "media_type": media_type,
                "warnings": warnings,
            }
            if scan_meta:
                payload["scan"] = scan_meta

            if suffix == ".zip":
                payload["packaged"] = True
                payload["note"] = "结果含抽取的图片，已打包为 ZIP，内含 document.md 与 assets/"
                if unpack:
                    extract_dir = destination.with_suffix("")
                    if extract_dir.exists() and not overwrite:
                        raise ConversionError("OUTPUT_EXISTS", f"解压目录已存在：{extract_dir}", 409)
                    extract_dir.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(destination) as archive:
                        archive.extractall(extract_dir)
                    document = extract_dir / "document.md"
                    payload["unpacked_dir"] = str(extract_dir)
                    if document.exists():
                        payload["document"] = str(document)
                        payload["assets_dir"] = str(extract_dir / "assets")
            return payload
    except ConversionError as exc:
        return {
            "input": str(source),
            "ok": False,
            "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable},
        }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="doc-convert",
        description="在 PDF / DOCX / DOC / Markdown / HTML / ODT / RTF / TXT 之间转换文档格式",
    )
    parser.add_argument(
        "-i",
        "--input",
        dest="input",
        nargs="+",
        type=Path,
        default=None,
        help="一个或多个待转换文件",
    )
    parser.add_argument("-t", "--to", help="目标格式：pdf/docx/doc/md/html/odt/rtf/txt")
    parser.add_argument("-o", "--output", type=str, help="输出文件或目录；省略则与输入同目录")
    parser.add_argument("--unpack", action="store_true", help="结果为 ZIP 包时自动解包")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出")
    parser.add_argument("--no-scan", action="store_true", help="扫描件不做 OCR 重建，直接返回 OCR_REQUIRED")
    parser.add_argument("--formats", action="store_true", help="列出支持的格式与限制后退出")
    parser.add_argument("--check-env", action="store_true", help="检查运行环境后退出")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    office = ensure_libreoffice()

    if args.check_env:
        python = ensure_engine()
        probe = subprocess.run(
            [str(python), "-c", "import docconvert,sys;print(docconvert.__version__ if hasattr(docconvert,'__version__') else 'unknown')"],
            capture_output=True,
            text=True,
            check=False,
        )
        print(
            json.dumps(
                {
                    "ready": probe.returncode == 0,
                    "skill_root": str(SKILL_ROOT),
                    "environment": str(python),
                    "engine_version": probe.stdout.strip() or None,
                    "libreoffice": str(office) if office else None,
                    "pdf_engine": str(office or typst_binary() or "") or None,
                    "pdf_output": bool(office or typst_binary()),
                    "scan_ocr": os.name == "nt" and (SKILL_ROOT / "scripts" / "win_ocr.ps1").is_file(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if probe.returncode == 0 else 1

    if args.formats:
        reexec_if_needed(ensure_engine())
        print(json.dumps(supported_formats(), ensure_ascii=False, indent=2))
        return 0

    if not args.input or not args.to:
        print(json.dumps({"ok": False, "error": {"code": "BAD_ARGS", "message": "需要提供输入文件与 --to 目标格式"}}, ensure_ascii=False, indent=2))
        return 2

    output_arg: Path | None = None
    if args.output:
        raw = str(args.output)
        output_arg = Path(raw).expanduser()
        # 以分隔符结尾表示"放进这个目录"，目录不存在就先建好，避免被当成文件名
        if raw.endswith(("/", "\\")):
            output_arg.mkdir(parents=True, exist_ok=True)

    if len(args.input) > 1 and output_arg is not None and not output_arg.is_dir():
        print(
            json.dumps(
                {"ok": False, "error": {"code": "BAD_ARGS", "message": "多个输入文件时 --output 必须是目录"}},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    reexec_if_needed(ensure_engine())

    results = [
        convert_one(item, args.to, output_arg, args.unpack, args.overwrite, allow_scan=not args.no_scan)
        for item in args.input
    ]
    failed = [item for item in results if not item["ok"]]
    payload = {"ok": not failed, "total": len(results), "succeeded": len(results) - len(failed), "failed": len(failed), "results": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if failed:
        return 1 if len(failed) == len(results) else 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
