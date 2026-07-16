"""file_parser 的单元测试：覆盖 .txt / .docx / .pdf / 不支持格式 / 空文件 / 大小限制。

核心逻辑放在同步函数 _parse_content 中，测试直接调用，无需 pytest-asyncio 插件；
仅 UploadFile 读字节路径用 asyncio.run 覆盖。
"""
import asyncio
import io

import pytest
from starlette.datastructures import UploadFile

from utils.file_parser import (
    parse_uploaded_file,
    _parse_content,
    _extract_xlsx_bytes,
    SUPPORTED_EXTENSIONS,
    MAX_FILE_BYTES,
)


def _make_docx_bytes() -> bytes:
    """在内存中构造一个最小 .docx（含段落与表格），返回字节。"""
    from docx import Document
    doc = Document()
    doc.add_paragraph("用户登录需求")
    doc.add_paragraph("1. 输入正确账号密码可登录成功。")
    doc.add_paragraph("2. 密码错误时提示错误。")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "字段"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "username"
    table.cell(1, 1).text = "用户名"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_parse_txt():
    text = _parse_content("req.txt", "需求A\n需求B".encode("utf-8"))
    assert "需求A" in text and "需求B" in text


def test_parse_txt_gbk_fallback():
    text = _parse_content("req_gbk.txt", "中文需求内容".encode("gbk"))
    assert "中文需求内容" in text


def test_parse_md_as_text():
    text = _parse_content("spec.md", "# 标题\n- 列表项".encode("utf-8"))
    assert "# 标题" in text and "列表项" in text


def test_parse_docx_paragraphs_and_table():
    text = _parse_content("login.docx", _make_docx_bytes())
    assert "用户登录需求" in text
    assert "输入正确账号密码可登录成功" in text
    # 表格单元格也应被抽出
    assert "username" in text and "用户名" in text


def _make_xlsx_bytes() -> bytes:
    """在内存中构造一个最小 .xlsx（含 sheet1 与游离的 sheet2），返回字节。

    sheet2 故意不写进 workbook.xml，用来验证 _extract_xlsx_bytes 能覆盖
    openpyxl 会漏读的「游离工作表」。
    """
    import zipfile

    shared = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'count="6" uniqueCount="6">'
        '<si><t>名称</t></si><si><t>数量</t></si><si><t>苹果</t></si>'
        '<si><t>5</t></si><si><t>附表</t></si><si><t>值</t></si></sst>'
    )
    sheet1 = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>'
        '</sheetData></worksheet>'
    )
    sheet2 = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        '<row r="1"><c r="A1" t="s"><v>4</v></c><c r="B1" t="s"><v>5</v></c></row>'
        '</sheetData></worksheet>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/worksheets/sheet1.xml", sheet1)
        z.writestr("xl/worksheets/sheet2.xml", sheet2)  # 游离表，无 workbook.xml 引用
    return buf.getvalue()


def test_parse_xlsx_both_sheets_including_orphan():
    text = _extract_xlsx_bytes(_make_xlsx_bytes())
    # sheet1 内容
    assert "名称" in text and "数量" in text and "苹果" in text and "5" in text
    # 游离的 sheet2 也应被抽到
    assert "附表" in text and "值" in text


def _make_pdf_bytes() -> bytes:
    """在内存中构造一个最小有效 PDF（单页、含一行文本），返回字节。"""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = b"BT /F1 12 Tf 72 720 Td (Hello PDF requirement) Tj ET"
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(pdf)
    pdf += b"xref\n"
    pdf += f"0 {len(objects) + 1}\n".encode()
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += b"trailer\n<< /Root 1 0 R >>\n"
    pdf += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    return pdf


def test_parse_pdf():
    text = _parse_content("spec.pdf", _make_pdf_bytes())
    assert "Hello PDF requirement" in text


def test_unsupported_extension_raises():
    with pytest.raises(ValueError):
        _parse_content("note.xlsx", b"PK fake xlsx")


def test_empty_file_raises():
    with pytest.raises(ValueError):
        _parse_content("empty.txt", b"")


def test_oversize_raises():
    big = b"x" * (MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError):
        _parse_content("big.txt", big)


def test_supported_extensions():
    assert ".docx" in SUPPORTED_EXTENSIONS
    assert ".pdf" in SUPPORTED_EXTENSIONS
    assert ".txt" in SUPPORTED_EXTENSIONS
    assert ".md" in SUPPORTED_EXTENSIONS


def test_parse_uploaded_file_none_returns_empty():
    assert asyncio.run(parse_uploaded_file(None)) == ""


def test_parse_uploaded_file_reads_bytes():
    up = UploadFile(filename="login.docx", file=io.BytesIO(_make_docx_bytes()))
    text = asyncio.run(parse_uploaded_file(up))
    assert "用户登录需求" in text
