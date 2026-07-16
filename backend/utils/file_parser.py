"""
上传需求文件的解析工具。

支持格式：
  - .docx : 现代 Word 文档（python-docx 抽取段落与表格；Word 中嵌入的 Excel 对象一并抽取）
  - .pdf  : PDF 文档（pdfplumber 抽取每页文本 + 表格，表格重建为 markdown）
  - .txt  : 纯文本（UTF-8，失败回退 gbk/gb18030/latin-1）
  - .md   : Markdown，按纯文本读取

解析出的纯文本会作为生成测试用例的「需求主体」送入模型。
注意：仅能抽取「文本型」PDF。扫描件/纯图片型 PDF，以及用矢量图形/图片绘制的表格（无文字层），纯文本抽取读不到内容（需 OCR），会返回空内容并触发「解析后内容为空」校验报错。
"""
import io
import logging
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Optional

from fastapi import UploadFile

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}
MAX_FILE_BYTES = 10 * 1024 * 1024      # 单文件上限 10MB
MAX_CONTENT_CHARS = 120_000            # 解析文本上限，约 30k token，超出截断

# Word 把「在线/链接型电子表格」以图片形式嵌入时，会附带此提示性说明，
# 不是真实需求内容，解析 docx 时直接剔除。
_EMBED_IMAGE_CAPTIONS = ("点击图片可查看完整电子表格",)


def _col_letter_to_idx(ref: str) -> int:
    """把单元格引用里的列字母转成 1-based 列号，如 'B2' -> 2。"""
    m = re.match(r"([A-Za-z]+)", ref)
    letters = m.group(1).upper() if m else ""
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - 64)
    return col


def _extract_xlsx_bytes(blob: bytes) -> str:
    """从嵌入的 Excel(.xlsx/.xlsm) 字节抽取各工作表，重建为 markdown 表格。

    直接解析 xlsx 压缩包内的 worksheet XML + sharedStrings，而不是依赖 openpyxl——
    这样能覆盖 workbook.xml 未登记、但实际存在于压缩包中的「游离工作表」（某些
    Word 嵌入对象会出现此情况，导致 openpyxl 漏读真实数据）。仅用标准库，无额外依赖。
    """
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(blob))

    # 共享字符串表
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        try:
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sroot.findall(f"{ns}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        except ET.ParseError:
            pass

    # 工作表显示名（按 workbook.xml 中 <sheet> 顺序）
    sheet_names: list[str] = []
    if "xl/workbook.xml" in z.namelist():
        try:
            wroot = ET.fromstring(z.read("xl/workbook.xml"))
            sheet_names = [s.get("name", "") for s in wroot.iter(f"{ns}sheet")]
        except ET.ParseError:
            pass

    # 遍历压缩包中所有 worksheet（含游离表）
    sheet_files = sorted(
        (n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
        key=lambda n: int(re.search(r"(\d+)", n).group(1)),
    )

    blocks: list[str] = []
    for idx, sfile in enumerate(sheet_files):
        try:
            root = ET.fromstring(z.read(sfile))
        except ET.ParseError:
            continue
        sd = root.find(f"{ns}sheetData")
        if sd is None:
            continue
        grid: dict[tuple[int, int], str] = {}
        max_col = 0
        for row in sd.findall(f"{ns}row"):
            r = int(row.get("r", "0"))
            col_cursor = 0
            for c in row.findall(f"{ns}c"):
                ref = c.get("r")
                if ref:
                    col = _col_letter_to_idx(ref)
                else:
                    col_cursor += 1
                    col = col_cursor
                col_cursor = max(col_cursor, col)
                max_col = max(max_col, col)

                ctype = c.get("t")
                v = c.find(f"{ns}v")
                is_node = c.find(f"{ns}is")
                if ctype == "s" and v is not None and v.text is not None:
                    val = shared[int(v.text)] if v.text.lstrip("-").isdigit() else ""
                elif is_node is not None:
                    val = "".join(t.text or "" for t in is_node.iter(f"{ns}t"))
                else:
                    val = v.text if v is not None else ""
                grid[(r, col)] = (val or "").strip()

        if not grid:
            continue
        max_row = max(r for r, _ in grid)
        rows = []
        for r in range(1, max_row + 1):
            rows.append([grid.get((r, c), "") for c in range(1, max_col + 1)])
        rows = [r for r in rows if any(r)]  # 丢弃全空行
        if not rows:
            continue
        n_cols = max(len(r) for r in rows)
        norm = [r + [""] * (n_cols - len(r)) for r in rows]

        md = ["| " + " | ".join(norm[0]) + " |"]
        md.append("|" + "---|" * n_cols)
        for r in norm[1:]:
            md.append("| " + " | ".join(r) + " |")
        blocks.append("\n".join(md))

    return "\n\n".join(blocks)


def _extract_docx(content: bytes) -> str:
    """从 .docx 字节抽取段落、表格，以及嵌入的 Excel 对象。"""
    from docx import Document

    doc = Document(io.BytesIO(content))
    parts: list[str] = []
    for para in doc.paragraphs:
        text = para.text
        if not text or not text.strip():
            continue
        text = text.strip()
        # Word 在嵌入「在线/链接型电子表格」并以图片展示时，会附带此提示性
        # 说明，非真实需求内容，解析时剔除。
        if text in _EMBED_IMAGE_CAPTIONS:
            continue
        parts.append(text)

    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            line = " | ".join(cells)
            if line.strip():
                parts.append(line)

    # 抽取 Word 中嵌套/嵌入的 Excel 对象（位于 package 的 .xlsx/.xlsm 部件）
    try:
        for part in doc.part.package.iter_parts():
            name = str(part.partname).lower()
            if name.endswith(".xlsx") or name.endswith(".xlsm"):
                xlsx_text = _extract_xlsx_bytes(part.blob)
                if xlsx_text.strip():
                    parts.append(xlsx_text)
    except Exception:  # 嵌入解析失败时不影响正文解析
        logger.warning("解析 docx 中嵌入的 Excel 失败，已跳过", exc_info=True)

    return "\n".join(parts)


def _extract_pdf(content: bytes) -> str:
    """从 PDF 字节抽取文本与表格（pdfplumber）。

    - 文本按页抽取（extract_text）。
    - 每个检测到的表格用 markdown 表格形式重建，保留行列结构，便于模型理解。
    - 扫描件/图片型 PDF 无文本层时各页文本为空，由上层「解析后内容为空」校验拦截。
    """
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                parts.append(text)

            # 抽取表格并重建为 markdown 表格（保留表头与行列）
            for table in page.extract_tables():
                if not table:
                    continue
                # 跳过完全为空的表
                if all(not (cell and str(cell).strip()) for row in table for cell in row):
                    continue
                header = table[0]
                n_cols = len(header)
                md = [
                    "| " + " | ".join(str(c or "").strip() for c in header) + " |",
                    "|" + "---|" * n_cols,
                ]
                for row in table[1:]:
                    md.append("| " + " | ".join(str(c or "").strip() for c in row) + " |")
                parts.append("\n".join(md))
    return "\n".join(parts)


def _decode_text(content: bytes) -> str:
    """按候选编码依次尝试解码文本，尽量兼容中文 Windows 默认编码。"""
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # 最后的兜底：替换不可解码字节
    return content.decode("utf-8", errors="replace")


def _parse_content(filename: str, content: bytes) -> str:
    """解析已读取的文件字节为纯文本（同步核心，便于测试与复用）。

    解析失败（格式不支持 / 空文件 / 过大 / 无内容）抛 ValueError。
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"不支持的文件格式：{ext or '未知'}。"
            f"仅支持 {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if not content:
        raise ValueError("上传的文件为空，请确认文件包含内容")

    if len(content) > MAX_FILE_BYTES:
        raise ValueError(
            f"文件过大（约 {len(content) // 1024}KB），上限 {MAX_FILE_BYTES // 1024 // 1024}MB"
        )

    if ext == ".docx":
        text = _extract_docx(content)
    elif ext == ".pdf":
        text = _extract_pdf(content)
    else:
        text = _decode_text(content)

    text = text.strip()
    if not text:
        raise ValueError("解析后内容为空，请确认文件包含可读文本")

    if len(text) > MAX_CONTENT_CHARS:
        logger.warning(f"上传文件内容超长，已截断至 {MAX_CONTENT_CHARS} 字符")
        text = text[:MAX_CONTENT_CHARS]

    return text


async def parse_uploaded_file(file: Optional[UploadFile]) -> str:
    """解析上传的需求文件为纯文本。

    返回空串表示无文件；其余校验与 `_parse_content` 一致。
    """
    if file is None:
        return ""

    content = await file.read()
    return _parse_content(file.filename or "", content)
