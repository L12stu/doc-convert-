---
name: doc-convert
version: "1.0.0"
display_name: DocConvert 文档格式转换
display_name_en: DocConvert
description: Convert documents between PDF, Word (DOCX/DOC), Markdown, HTML, ODT, RTF and TXT using a bundled local conversion engine that preserves headings, lists, tables and images. This skill should be used when the user asks to convert or transform a document's file format, extract Markdown from a PDF, turn Markdown or HTML into Word, batch-convert several files at once, or produce a PDF from an Office document; it also applies to image-only/scanned PDFs that must be rebuilt into editable Word or Markdown through local OCR (results are flagged as needing review), and when the user asks which conversion directions are supported or why a conversion failed.
description_zh: "本地文档格式互转：PDF、Word、Markdown、HTML、ODT、RTF、TXT 全向转换，支持批量；扫描件 PDF 可自动识别重建为可编辑文档，结果标注需校对。"
description_en: "Convert PDF, Word, Markdown, HTML, ODT, RTF and TXT locally, including batch jobs; scanned PDFs are rebuilt into editable documents through OCR and flagged for review."
agent_created: true
---

# Doc Convert

## Core rule

**用户不需要运行任何命令。** 用户只会说一句话，例如"把这个转成 Word"、"这个 PDF 转 Markdown"、"转成 PDF"。

你的职责是：自己调用脚本完成转换，然后把**成品文件**直接交给用户。不要把命令行、参数、脚本路径、虚拟环境、依赖安装贴给用户，也不要让用户"自己跑一下"。

转换质量分三档，向用户说明时不要混用：Office → PDF 是版式保真；PDF → DOCX 是可编辑重建；PDF → Markdown/TXT 与 Markdown/TXT 互转是语义保真。矩阵见 `references/formats.md`。

## When to use

- 用户要求转换文档格式："转成 PDF"、"PDF 转 Markdown"、"导出成 Word"、"换个格式"、"转一下"
- 用户要求从 PDF 抽取可编辑内容
- 用户要求批量转换多个文件到同一格式
- 用户给的是图片版/扫描版 PDF（选中不了文字），要转成可编辑文档——自动走扫描件重建
- 用户询问支持哪些格式、某个转换为什么失败、某个错误码含义

不使用：

- 只读取或总结内容而不改格式（用文档读取能力）
- 生成全新文档而非格式转换
- 对扫描件做"精确还原原版式"的承诺（重建只保证内容可编辑，不保证版式一致）

## How to execute

1. **定位输入文件**。用户在对话里附带的文件，路径通常已经给出；若用户只说了格式没给文件，直接问他一句要哪个文件即可（这是唯一该问的情况）。
2. **判断目标格式**。用户说"转成 Word"→ `docx`，"转成 PDF"→ `pdf`，"转成 Markdown"→ `md`，"文本/纯文本"→ `txt`。没说就按上下文最近的格式意图。
3. **执行转换**（内部调用，不展示给用户）：

```bash
python <skill_dir>/scripts/convert.py -i "<输入文件绝对路径>" -t <目标格式> -o "<输出路径>"
```

4. **交付结果**。成功后立刻用 `present_files` 把产物交给用户，再用一句话说明结果。转换这类需求不需要用户读长文，通常一两句即可。
5. **失败时**：按 `Handling failures` 给一句人话说明，并给出能立刻用的替代方案，不要复述底层错误栈。

扫描件（无文字层）会由脚本自动接管，不必另行判断：引擎报告 `OCR_REQUIRED` 时，只要目标是 `docx`/`md`/`txt`/`html`，就会自动改走 OCR 重建，返回里带 `scan` 字段。**这种情况下必须转述 `warnings`，并明确告诉用户"需要校对"**，详见下文「扫描件」。

### 输出路径怎么定

- 用户指定了位置 → 用它。
- 用户没指定 → 输出到输入文件同目录，文件名沿用原名换扩展名。
- 结果为单位 ZIP（Markdown 带图）→ 加 `--unpack` 解包，然后交付其中的 `document.md`；若用户要整套素材，交付解包目录。

### 批量

多个文件一次转换时 `-o` 必须是目录：

```bash
python <skill_dir>/scripts/convert.py -i "a.docx" "b.md" -t pdf -o "D:/out/"
```

同批次输入若同名（`a.md` 与 `a.docx`），会得到同名产物并返回 `OUTPUT_EXISTS`。分别指定输出目录即可，不要用 `--overwrite` 硬盖。

## 扫描件（无文字层）

这类 PDF 整页是图片，抽不到字。脚本会渲染成图、调系统 OCR、按坐标重组版面，输出可编辑初稿。

**这不是"转换"，是"重建"：字符可能错，版式会变。** 交付时必须换一套说法，不能沿用前面三档保真的口径。

拿到 `scan` 字段后按三步判断：

1. `quality.latin_suspect_ratio > 0.02`：英文有可察觉误差，英文段落必须逐段核对。
2. `quality.chars_per_page < 80`：整页基本没识别出来（图片过淡、倾斜、手写），不要交付产物，直接说明原因。
3. `quality.two_column_pages > 0`：说明触发了双栏判定，把结果翻到那几页看有没有串行。

`needs_review` 恒为 `true`，不要对用户说"已完美转换"。

**质量不达标时的正确做法**：英文密集的扫描件（试卷、论文、合同）字母错误率会明显偏高，此时应逐页对照原图校对关键段落后再交付，不能把初稿直接给用户。校对只需覆盖英文段落与专有名词，中文部分通常可直接用。

## 对用户怎么说话

- 成功：一句结果 + 文件。例如"已转好，Word 版在下面。"不要贴 JSON。
- 扫描件成功：一句结果 + 一句实情 + 文件。例如"稿子好了，是识别重建的，个别字可能不准，英文段落建议扫一眼。"
- 分批/多文件：一句话说清成功了几个、哪几个失败及原因。
- 失败：一句人话 + 下一步。例如"这份 PDF 是加密的，得先解密我才能读。"
- **不要**说：装了什么依赖、建了 venv、用了哪个脚本、跑了什么命令、环境变量怎么配。这些是内部细节。
- 只有用户主动问"怎么实现的/能不能集成到别处"时，才展开技术说明。

## Input contract（内部参数，用户无需知道）

| 参数 | 必填 | 说明 |
|---|---|---|
| `-i, --input` | 是 | 一个或多个文件路径 |
| `-t, --to` | 是 | `pdf`/`docx`/`doc`/`md`/`html`/`odt`/`rtf`/`txt` |
| `-o, --output` | 否 | 输出文件或目录；省略则写到输入同目录 |
| `--unpack` | 否 | 结果为 ZIP 时自动解包 |
| `--overwrite` | 否 | 允许覆盖已存在输出，默认拒绝 |
| `--no-scan` | 否 | 禁止扫描件重建，直接返回 `OCR_REQUIRED` |
| `--formats` | 否 | 打印支持格式与限制 |
| `--check-env` | 否 | 打印环境状态 |

输入文件必须存在于本地磁盘。真实类型按文件头识别，扩展名仅作参考。

## Output contract

标准输出为 JSON，退出码 `0` 全成功 / `1` 全失败 / `2` 参数错误 / `3` 部分成功。

成功：

```json
{"ok": true, "total": 1, "succeeded": 1, "failed": 0,
 "results": [{"input": "D:/docs/report.docx", "ok": true, "target": "pdf",
              "output": "D:/docs/report.pdf", "media_type": "application/pdf", "warnings": []}]}
```

目标为 `md` 且含图片时，`output` 指向 ZIP：

```json
{"packaged": true, "note": "结果含抽取的图片，已打包为 ZIP，内含 document.md 与 assets/",
 "unpacked_dir": "D:/out/report_md", "document": "D:/out/report_md/document.md",
 "assets_dir": "D:/out/report_md/assets"}
```

扫描件重建成功时，结果项额外带 `scan`：

```json
{"input": "D:/docs/scan.pdf", "ok": true, "target": "docx", "output": "D:/out/scan.docx",
 "scan": {"engine": "windows-ocr", "pages": 19, "dpi": 300, "needs_review": true,
          "quality": {"chars_per_page": 2124.1, "latin_suspect_ratio": 0.0289,
                      "cjk_gap_count": 0, "two_column_pages": 3, "paragraphs": 267,
                      "needs_review": true, "warnings": []}}}
```

失败项：

```json
{"input": "...", "ok": false,
 "error": {"code": "OCR_REQUIRED", "message": "未检测到足够文本，可能是扫描件", "retryable": false}}
```

`warnings` 是内容保真提示，应转述给用户，不要丢弃。

## Behavior rules

- 输出扩展名以引擎真实产物为准。用户要 `.md` 但结果实为 ZIP 时自动改 `.zip` 并说明。
- 默认不覆盖已存在文件（`OUTPUT_EXISTS`）。要覆盖必须显式 `--overwrite`，且不要在用户未同意时默认覆盖。
- 输出不得覆盖输入文件（`OUTPUT_OVERWRITE`）。
- 多输入时 `-o` 必须是目录，否则 `BAD_ARGS` 退出码 2。
- 批量逐个执行，单个失败不影响其他，以 `results` 各项为准。
- 结果先落临时目录，成功后才复制到目标路径，失败不留半成品。

## Environment（排障用，不对用户展开）

首次调用在用户缓存目录建独立 venv 装引擎，之后复用：Windows `%LOCALAPPDATA%\doc-convert-skill`，其他平台 `~/.cache/doc-convert-skill`。`DOCCONVERT_SKILL_HOME` 可改位置，`DOCCONVERT_PIP_INDEX` 可改镜像（默认清华，失败自动回退官方源）。

运行要求：Python 3.11+（引擎包 `Requires-Python >=3.11`），需要 `venv` 模块；扫描件重建另需 Windows 10/11 与中文 OCR 语言包。首次安装约 150 MB 下载、落盘约 500 MB，其中 Pandoc 与 OpenCV 占主要部分，均属转换功能必需。

依赖分两层，都由 `scripts/convert.py` 自动处理，用户无需干预：

| 层 | 内容 | 安装方式 |
|---|---|---|
| 引擎包 | `assets/docconvert-*.whl`（本 skill 自身代码） | `pip install --no-deps`，避免拉入其元数据中钉住的服务端组件 |
| 运行时库 | `assets/requirements.txt`：pymupdf、pymupdf4llm、pdf2docx、python-docx、pypandoc-binary、pydantic、pydantic-settings、defusedxml | `pip install -r`，传输依赖由 pip 按各包元数据解析 |

引擎包元数据里另有 FastAPI、Redis、RQ、boto3、uvicorn 等服务端依赖，本 skill 不会导入，因此不安装。若需改动依赖，只改 `assets/requirements.txt`，不要给引擎包加新依赖。

输出 PDF 有两级引擎，用户侧无感：

1. 系统装了 LibreOffice → 版式保真输出。
2. 没有 LibreOffice → 自动准备内置 Typst 引擎（约 20 MB，首次一次性下载），由 pandoc 重新排版输出。中文、标题、列表、表格、图片均保留，但**不是原文档的像素级版式还原**，必须把这条 warning 转述给用户。

`doc`/`odt`/`rtf` 输出仍依赖 LibreOffice，缺失时返回 `LIBREOFFICE_UNAVAILABLE`。

Typst 从 GitHub Releases 下载，受限网络可能失败（API 返回 502 或超时即属此类）。失败时 PDF 目标同样返回 `LIBREOFFICE_UNAVAILABLE`，此时改出 DOCX/HTML，不要反复重试。若系统 PATH 里已有 `typst` 命令，会优先复用。

## Handling failures

按此表转成一句人话给用户，不要复述错误码给他看：

| 情况 | 对用户怎么说 / 怎么做 |
|---|---|
| `OCR_REQUIRED` | 目标格式不支持重建（非 docx/md/txt/html）或加了 `--no-scan`："这份是扫描件，没有文字层。要 Word 或 Markdown 我可以按图片识别重建一份，但得你自己过一遍。" |
| `SCAN_UNSUPPORTED` | 非 Windows 或缺 OCR 组件："这台机器没法做图片文字识别，只能给你有文字层的 PDF。" |
| `SCAN_FAILED` | 先重试一次；仍失败说明是文件图片质量或系统识别服务的问题 |
| `SCAN_EMPTY` | "整份没识别出字，可能是空白页或图片太模糊，换个清楚的版本我再试。" |
| `LIBREOFFICE_UNAVAILABLE` | 目标是 `doc`/`odt`/`rtf`："这几个老格式需要本机装 LibreOffice，换成 PDF 或 DOCX 我现在就能给你。"；目标是 `pdf` 且内置排版引擎没准备好："这台机器现在取不到 PDF 排版引擎（首次需联网下载一次），我先给你 DOCX 或 HTML。" |
| `PDF_ENGINE_FAILED` | "PDF 排版失败，我换个方式再试"——先重试一次，仍失败则改出 DOCX/HTML 并说明 |
| `ENCRYPTED_*` | "文件加密了，需要先解密。" |
| `FILE_TOO_LARGE` / `TOO_MANY_PAGES` | 说清触发的是哪个上限，并直接给出放宽后的处理 |
| `ZIP_BOMB` / `ARCHIVE_*` | "文件结构异常，拒绝处理。" |
| `REMOTE_RESOURCE_BLOCKED` | "文档引用了网络图片，需要改成放在文档里。" |
| `CONVERSION_TIMEOUT` | 先重试一次；仍失败则说明文件过大或过于复杂 |
| `SAME_FORMAT` | 无需转换，直接说明 |

完整错误码表见 `references/formats.md` 第 6 节。

## Resources

```
doc-convert/
├── SKILL.md                          # 技能定义：触发条件、执行流程、参数契约、话术
├── scripts/
│   ├── convert.py                    # 唯一入口：环境自举 + 参数解析 + 转换 + 落盘 + JSON 报告
│   ├── scan_pdf.py                   # 扫描件重建：渲染 → 系统 OCR → 版面重组 → Markdown + 质量指标
│   ├── win_ocr.ps1                   # Windows 内置 OCR 调用（scan_pdf.py 内部使用，不单独运行）
│   └── md_to_docx.py                 # 结构化 Markdown → DOCX 排版
├── references/
│   └── formats.md                    # 格式矩阵、三档保真、扫描链路、依赖路由、限制、错误码、ZIP 规则
└── assets/
    ├── docconvert-1.0.0-py3-none-any.whl   # 转换引擎分发包
    └── requirements.txt                    # 运行时依赖清单
```

- 入口固定为 `scripts/convert.py`，其余脚本由它调用，不要绕过入口单独执行。
- `references/formats.md` 按需加载，不要在转换成功时整篇读入。
- `assets/` 内文件不得改名：`convert.py` 按 `docconvert-*.whl` 和 `requirements.txt` 两个模式定位。
