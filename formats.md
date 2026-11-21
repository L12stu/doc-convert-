# 格式矩阵、保真边界与限制

## 1. 支持的格式

输入与输出格式完全相同，除"同格式转换"外任意两种之间都可路由。

| 扩展名 | 格式 | 输出形态 |
|---|---|---|
| `.pdf` | PDF | 单文件 |
| `.docx` | Word 2007+ | 单文件 |
| `.doc` | Word 97-2003 | 单文件 |
| `.md` | Markdown | 单文件；含抽取图片时为 ZIP 包 |
| `.html` | HTML | 单文件 |
| `.odt` | OpenDocument 文本文档 | 单文件 |
| `.rtf` | RTF | 单文件 |
| `.txt` | 纯文本 | 单文件 |

文件真实类型按魔数识别，不信任扩展名。扩展名与实际内容不符时按真实类型处理。

## 2. 保真口径（必须如实向用户说明）

转换质量分三档，不要用同一套话术承诺：

| 转换方向 | 保真程度 | 说明 |
|---|---|---|
| Office → PDF | 版式保真（有 LibreOffice）/ 重新排版（无则 Typst） | 前者保留页面尺寸、分页、图片、表格；后者保留标题/列表/表格/图片，但版式按新引擎重排 |
| PDF → DOCX | 可编辑重建 | 数字文本型 PDF 的结构与版式重建，不是无损逆向 |
| PDF → Markdown / TXT | 语义重建 | 保留标题、列表、表格、图片，不保留精确坐标与页面尺寸 |
| Markdown / HTML / TXT → 其他 | 语义保真 | 保留标题层级、列表、表格、图片等结构语义 |

边界结论：

- 扫描件 PDF 没有可提取文本：目标为 `docx`/`md`/`txt`/`html` 时自动走 OCR 重建分支（见第 3 节），返回 `scan` 质量报告；其他目标或显式 `--no-scan` 时返回 `OCR_REQUIRED`。重建产物是"可编辑初稿"，不承诺字符全对，也不等于原版式还原。
- 多栏、浮动对象、复杂合并表格在 PDF 逆向时需人工复核，输出 `warnings` 会提示。
- Markdown 没有固定页面布局，转 PDF 时的分页由 LibreOffice 决定，无法与原文完全一致。

## 3. 扫描件重建链路

适用于整页是图片、抽不到文字层的 PDF（引擎抛 `OCR_REQUIRED` 的那类）。

流程：

1. 300 dpi 灰度渲染每页。
2. 调用 Windows 内置 OCR（默认 `zh-Hans-CN`），保留每行文字与坐标。
3. 版面重组：剔除页眉页脚；右侧窄栏文字作为旁注单独归位；同一水平线上的左右两段拼成一行；按行距与段首缩进切段；中文去字间空格；纯英文行的全角标点转半角并将 `, s` 还原为 `'s`。
4. 质量闸门：统计字符密度、英文可疑 token 比例、中文空格残留数，写入 `scan.quality`。

产出形态：

| 目标 | 产物 |
|---|---|
| `docx` | 分级标题 + 宋体正文的 Word，跨页断句自动接回 |
| `md` | 结构化 Markdown（标题、段落、旁注引用块） |
| `txt` | 去标记纯文本 |
| `html` | 带基础样式的单页 HTML |

必须如实说明的限制：

- 仅 Windows 可用，依赖系统 OCR 组件；其他平台返回 `SCAN_UNSUPPORTED`。
- 英文与衬线字体识别误差可察：`latin_suspect_ratio > 0.02` 时英文部分必须逐段校对。
- 旁注统一附在每页末尾，不保证落回原始位置。
- 双栏判定是基于坐标的启发式规则，极端版式可能串行。
- `needs_review` 恒为 `true`：产物是可编辑初稿，定稿前必须人工校对。

## 4. LibreOffice 依赖路由

skill 层对 PDF 输出做了两级处理：

- 目标为 `pdf` 且源不是 `pdf`：系统有 LibreOffice 时走版式保真；没有时自动用内置 Typst 引擎（pandoc → typst）重新排版，输出 `warnings` 标明"非原版式还原"。
- 目标为 `doc`、`odt`、`rtf`，或源为这三种格式且目标不同：必须宿主机有 LibreOffice，否则返回 `LIBREOFFICE_UNAVAILABLE`（503，可重试）。

其余链路（`pdf ↔ docx/md/txt`、`docx ↔ md/html/txt` 等）由 Python 引擎与内置 Pandoc 完成，不需要任何外部程序。

转换引擎的 venv 只装 Python 依赖，**不包含 LibreOffice**；PDF 兜底用的 Typst 二进制也在首次需要时自动下载到运行目录 `bin/`，不占 skill 包体积。

## 5. 默认限制

| 限制项 | 默认值 | 触发错误码 |
|---|---|---|
| 单文件大小 | 50 MiB | `FILE_TOO_LARGE` |
| PDF 页数 | 300 页 | `TOO_MANY_PAGES` |
| 输出大小 | 200 MiB | `OUTPUT_TOO_LARGE` |
| 单次转换超时 | 120 秒 | `CONVERSION_TIMEOUT` |
| Office 解压后总大小 | 500 MiB | `ARCHIVE_TOO_LARGE` |
| Office 内部条目数 | 2000 | `ARCHIVE_TOO_COMPLEX` |
| 压缩比 | 200 | `ZIP_BOMB` |
| Markdown/HTML 附带资源数 | 50 | `TOO_MANY_ASSETS` |

限制可通过 `DOCCONVERT_` 前缀的环境变量覆盖，例如 `DOCCONVERT_MAX_FILE_SIZE=104857600`。

## 6. 错误码

| 错误码 | 含义 | 可重试 |
|---|---|---|
| `SAME_FORMAT` | 输入输出格式相同 | 否 |
| `UNSUPPORTED_FORMAT` | 格式不在支持列表 | 否 |
| `SOURCE_NOT_FOUND` | 输入文件不存在 | 否 |
| `EMPTY_FILE` | 文件为 0 字节 | 否 |
| `FORMAT_MISMATCH` | 扩展名与真实类型不符且无法判定 | 否 |
| `ENCRYPTED_PDF` | PDF 已加密 | 否 |
| `ENCRYPTED_DOCUMENT` | Office 文档已加密 | 否 |
| `CORRUPT_PDF` | PDF 结构损坏 | 否 |
| `CORRUPT_DOCUMENT` | Office 文档结构损坏 | 否 |
| `ZIP_BOMB` | 压缩比异常，疑似压缩炸弹 | 否 |
| `ARCHIVE_TOO_LARGE` | 解压后超过 500 MiB | 否 |
| `ARCHIVE_TOO_COMPLEX` | 内部条目超过 2000 | 否 |
| `TOO_MANY_PAGES` | PDF 页数超过上限 | 否 |
| `OCR_REQUIRED` | 扫描件，无可用文本层（目标不支持重建或已禁用扫描分支） | 否（需外部 OCR） |
| `SCAN_UNSUPPORTED` | 扫描件重建不可用（非 Windows 或缺 OCR 组件） | 否 |
| `SCAN_FAILED` | 系统 OCR 调用失败 | 是（可重试一次） |
| `SCAN_EMPTY` | 扫描件未识别出任何文字 | 否 |
| `REMOTE_RESOURCE_BLOCKED` | 引用了远程图片或资源 | 否 |
| `UNSAFE_ASSET_PATH` | 资源路径含穿越或绝对路径 | 否 |
| `PDF_EXTRACTION_FAILED` | PDF 文本/图片抽取失败 | 否 |
| `EMPTY_DOCUMENT` | 文档无有效内容 | 否 |
| `EMPTY_OUTPUT` | 转换结果为空 | 否 |
| `LIBREOFFICE_UNAVAILABLE` | 需要 LibreOffice 但未安装 | 是（装后重试） |
| `PDF_ENGINE_FAILED` | PDF 排版引擎执行失败 | 是（可重试一次） |
| `PANDOC_UNAVAILABLE` | 内置 Pandoc 不可用 | 是 |
| `ENGINE_FAILED` | 引擎执行失败 | 是 |
| `ENGINE_START_FAILED` | 引擎无法启动 | 是 |
| `CONVERSION_TIMEOUT` | 超过 120 秒 | 是 |
| `OUTPUT_EXISTS` | 输出已存在且未加 `--overwrite` | 否 |
| `OUTPUT_OVERWRITE` | 输出会覆盖输入文件 | 否 |

## 7. Markdown 输出打包规则

目标为 `md` 且文档含图片时，引擎必须把图片与正文一起交付，因此产物是 ZIP 包：

```
<name>.md.zip
├── document.md
└── assets/
    └── <原文档名>-<页>-<序号>.png
```

不含图片时直接输出单个 `.md` 文件。加 `--unpack` 可让脚本自动解包到同名目录，返回其中的 `document.md` 路径。

`document.md` 内部以相对路径引用 `assets/`，解包后保持同级关系即可正常显示图片，不要单独移动其中一半。
