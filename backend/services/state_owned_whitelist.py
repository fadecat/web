"""国资白名单 Excel 解析(正股央国企标注用)。

自 market-daily src/common/whitelist.py 移植(裁剪):手写 zip+xml 解析
(stdlib zipfile + ElementTree,非 openpyxl)。白名单文件默认
``config/whitelist/state_owned_whitelist.xlsx``,可用环境变量
``STATE_OWNED_WHITELIST_XLSX`` 覆盖;名单更新直接覆盖该文件并 git commit
(与 market-daily 同一约定)。

与上游的差异:
- 仅保留企业性质映射所需入口(``load_stock_enterprise_nature_map_from_xlsx``),
  代码白名单 frozenset 加载器未移植;
- ``enterprise_nature_map()`` 对文件缺失/解析失败一律返回空映射(不抛异常),
  保证筛选管线缺名单时只少一个标注列,不中断;
- 环境变量读取用 ``os.getenv``(本仓无 market-daily 的 common.env 层)。
"""
from __future__ import annotations

import os
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WHITELIST_XLSX = _REPO_ROOT / "config" / "whitelist" / "state_owned_whitelist.xlsx"

XLSX_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
XLSX_REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}


def normalize_stock_code(value: Any) -> str:
    """提取数字部分并左补零至 6 位;无数字返回空串。"""
    digits = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)


def _xlsx_sheet_path(zf: zipfile.ZipFile, sheet_name: str | None = None) -> str:
    workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels_root.findall("r:Relationship", XLSX_REL_NS)
    }

    sheets = workbook_root.find("s:sheets", XLSX_NS)
    if sheets is None:
        raise RuntimeError("Excel 缺少 sheets 节点")

    sheet_nodes = sheets.findall("s:sheet", XLSX_NS)
    if not sheet_nodes:
        raise RuntimeError("Excel 没有可读取的工作表")

    target_node = sheet_nodes[0]
    if sheet_name:
        for node in sheet_nodes:
            if node.attrib.get("name") == sheet_name:
                target_node = node
                break
        else:
            raise RuntimeError(f"Excel 中不存在工作表: {sheet_name}")

    rel_id = target_node.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    target = rel_map.get(rel_id)
    if not target:
        raise RuntimeError(f"Excel 工作表关系缺失: {target_node.attrib.get('name', '')}")
    # Target 可能相对("worksheets/sheet1.xml")或绝对("/xl/worksheets/sheet1.xml"),
    # 统一归一到 zip 内 "xl/..." 路径。
    target = target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return "xl/" + target


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(cell.itertext()).strip()

    if cell_type == "s":
        index_text = cell.findtext("s:v", default="", namespaces=XLSX_NS).strip()
        if not index_text:
            return ""
        return shared_strings[int(index_text)]

    return cell.findtext("s:v", default="", namespaces=XLSX_NS).strip()


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []

    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall("s:si", XLSX_NS):
        values.append("".join(item.itertext()).strip())
    return values


@lru_cache(maxsize=8)
def load_stock_enterprise_nature_map_from_xlsx(
    xlsx_path: str | None = None,
    sheet_name: str | None = None,
    code_header: str = "代码",
    nature_header: str = "企业性质",
) -> dict[str, str]:
    """返回 ``{stock_code: 企业性质原文}``(如 中央国有企业 / 地方国有企业)。

    ``xlsx_path`` 为空时使用 ``STATE_OWNED_WHITELIST_XLSX`` 环境变量或默认路径。
    文件缺失抛 FileNotFoundError,格式问题抛 RuntimeError(带路径上下文)。
    """
    path = Path(xlsx_path or os.getenv("STATE_OWNED_WHITELIST_XLSX") or DEFAULT_WHITELIST_XLSX)
    if not path.exists():
        raise FileNotFoundError(f"未找到国资白名单文件: {path}")

    with zipfile.ZipFile(path) as zf:
        shared_strings = _xlsx_shared_strings(zf)
        sheet_path = _xlsx_sheet_path(zf, sheet_name=sheet_name)
        root = ET.fromstring(zf.read(sheet_path))

    rows = root.find("s:sheetData", XLSX_NS)
    if rows is None:
        raise RuntimeError(f"Excel 工作表为空: {path}")

    code_column: str | None = None
    nature_column: str | None = None
    nature_map: dict[str, str] = {}

    for row_index, row in enumerate(rows.findall("s:row", XLSX_NS), 1):
        current: dict[str, str] = {}
        for cell in row.findall("s:c", XLSX_NS):
            ref = cell.attrib.get("r", "")
            match = re.match(r"([A-Z]+)", ref)
            if not match:
                continue
            current[match.group(1)] = _xlsx_cell_value(cell, shared_strings)

        if row_index == 1:
            for column, header in current.items():
                if header == code_header:
                    code_column = column
                    break
            if code_column is None:
                raise RuntimeError(f"Excel 未找到 `{code_header}` 列: {path}")
            for column, header in current.items():
                if header == nature_header:
                    nature_column = column
                    break
            continue

        stock_code = normalize_stock_code(current.get(code_column, ""))
        if not stock_code or stock_code in nature_map:
            continue
        if nature_column:
            nature_map[stock_code] = str(current.get(nature_column, "")).strip()

    if not nature_map:
        raise RuntimeError(f"Excel 白名单为空: {path}")
    return nature_map


@lru_cache(maxsize=1)
def enterprise_nature_map() -> dict[str, str]:
    """缺名单安全的企业性质映射:文件缺失/解析失败返回空映射,不抛异常。

    显式解析路径传给加载器(而非依赖其默认参数),保证环境变量切换后
    内层 lru_cache 的键随之变化,不会命中切换前的旧名单。
    """
    path = os.getenv("STATE_OWNED_WHITELIST_XLSX") or str(DEFAULT_WHITELIST_XLSX)
    try:
        return dict(load_stock_enterprise_nature_map_from_xlsx(path))
    except (FileNotFoundError, RuntimeError, zipfile.BadZipFile, ET.ParseError):
        return {}
