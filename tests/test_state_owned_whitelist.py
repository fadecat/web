# -*- coding: utf-8 -*-
"""国资白名单解析测试(移植自 market-daily tests/test_common_whitelist.py, 裁剪)。

fixture xlsx 用 stdlib zipfile 手工构造(inlineStr 单元格, 无 openpyxl 依赖),
验证: 解析正确性 / 代码归一 / 重复去重 / 缺文件与缺列报错 / 缺名单安全兜底 /
cb_screen DTO 集成。真实名单文件存在时附加 sanity(不存在则跳过)。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from backend.services import state_owned_whitelist as wl
from backend.services.cb_screen import _to_dto

# ── 手工构造最小 xlsx(inlineStr, 解析器原生支持)──────────────────────────────
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '</Types>'
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    '</Relationships>'
)
_WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="Sheet1" r:id="rId1"/></sheets></workbook>'
)
_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    '</Relationships>'
)


def _cell(ref: str, text: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _sheet(rows: list[list[str]]) -> str:
    body = "".join(
        f'<row r="{i}">' + "".join(_cell(f"{col}{i}", text) for col, text in zip("ABC", row)) + "</row>"
        for i, row in enumerate(rows, 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{body}</sheetData></worksheet>'
    )


def _write_xlsx(path: Path, rows: list[list[str]]) -> str:
    parts = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "xl/workbook.xml": _WORKBOOK,
        "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS,
        "xl/worksheets/sheet1.xml": _sheet(rows),
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in parts.items():
            zf.writestr(name, content)
    return str(path)


@pytest.fixture()
def fixture_xlsx(test_artifact_dir):
    return _write_xlsx(test_artifact_dir / "whitelist.xlsx", [
        ["代码", "名称", "企业性质"],
        ["600001", "测试股", "中央国有企业"],
        ["1", "示例银行", "地方国有企业"],           # 归一为 000001
        ["600001", "重复代码应去重", "地方国有企业"],  # 重复 code 去重(保留首条)
        ["abc", "无数字代码跳过", "中央国有企业"],
    ])


# ── normalize_stock_code ──────────────────────────────────────────────────────
def test_normalize_stock_code():
    assert wl.normalize_stock_code("  600001.SH ") == "600001"
    assert wl.normalize_stock_code("1") == "000001"
    assert wl.normalize_stock_code("abc") == ""
    assert wl.normalize_stock_code(None) == ""


# ── 企业性质映射 ──────────────────────────────────────────────────────────────
def test_load_enterprise_nature_map(fixture_xlsx):
    nature_map = wl.load_stock_enterprise_nature_map_from_xlsx(fixture_xlsx)
    assert nature_map["600001"] == "中央国有企业"
    assert nature_map["000001"] == "地方国有企业"   # 代码归一后命中
    assert len(nature_map) == 2                     # 去重 + 无数字跳过


def test_load_missing_file_raises(test_artifact_dir):
    with pytest.raises(FileNotFoundError):
        wl.load_stock_enterprise_nature_map_from_xlsx(str(test_artifact_dir / "nope.xlsx"))


def test_load_missing_code_column_raises(test_artifact_dir):
    path = _write_xlsx(test_artifact_dir / "bad.xlsx", [["没有代码列", "名称", "企业性质"], ["600001", "x", "中央国有企业"]])
    with pytest.raises(RuntimeError):
        wl.load_stock_enterprise_nature_map_from_xlsx(path)


def test_enterprise_nature_map_fail_soft(test_artifact_dir, monkeypatch):
    """文件缺失 → 安全兜底返回空映射(不抛异常, 管线不中断)。"""
    monkeypatch.setenv("STATE_OWNED_WHITELIST_XLSX", str(test_artifact_dir / "nope.xlsx"))
    wl.enterprise_nature_map.cache_clear()
    try:
        assert wl.enterprise_nature_map() == {}
    finally:
        wl.enterprise_nature_map.cache_clear()


# ── cb_screen DTO 集成 ────────────────────────────────────────────────────────
def test_to_dto_carries_enterprise_nature(monkeypatch, fixture_xlsx):
    from backend.services import cb_screen

    monkeypatch.setattr(cb_screen, "enterprise_nature_map",
                        lambda: wl.load_stock_enterprise_nature_map_from_xlsx(fixture_xlsx))
    row = {"cell": {"bond_id": "110001", "bond_nm": "A债", "stock_id": "600001"},
           "total_score": 1.0}
    dto = _to_dto(row, 1, scored_mode=True)
    assert dto["enterprise_nature"] == "中央国有企业"

    dto_missing = _to_dto({"cell": {"bond_id": "110002"}, "total_score": None}, 2, scored_mode=False)
    assert dto_missing["enterprise_nature"] == ""


# ── 真实名单 sanity(文件存在则验证可加载)──────────────────────────────────────
def test_real_whitelist_loads():
    path = wl.DEFAULT_WHITELIST_XLSX
    if not path.exists():
        pytest.skip("真实白名单文件不存在")
    nature_map = wl.load_stock_enterprise_nature_map_from_xlsx(str(path))
    assert len(nature_map) > 0
    assert all(len(code) == 6 for code in nature_map)
    assert set(nature_map.values()) <= {"中央国有企业", "地方国有企业"}
