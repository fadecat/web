# -*- coding: utf-8 -*-
"""恢复副本验证脚本(第四阶段/T7).

接收源库与备份副本, 执行:
- 两边 PRAGMA integrity_check
- 业务表集合一致
- 每表行数一致
- 每表主键集合一致
- 备份副本结构(compare_schema)与 ORM 匹配

任何差异打印 `表名 + 差异类型` 并返回 1; 不修改任一文件。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


def _path(raw: str) -> Path:
    return Path(raw[len("sqlite:///"):]) if raw.startswith("sqlite:///") else Path(raw)


def _tables(db_path: Path) -> dict[str, set]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def _row_counts(db_path: Path, tables: set[str]) -> dict[str, int]:
    con = sqlite3.connect(db_path)
    try:
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()


def _pk_sets(db_path: Path, tables: set[str]) -> dict[str, set]:
    con = sqlite3.connect(db_path)
    try:
        result = {}
        for t in tables:
            rows = con.execute(f'PRAGMA table_info("{t}")').fetchall()
            # pk 列: 序 > 0(复合主键各列序号递增), 0 = 非主键
            result[t] = {r[1] for r in rows if r[5] > 0}
        return result
    finally:
        con.close()


def _integrity(db_path: Path) -> str:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("PRAGMA integrity_check").fetchall()
        return ", ".join(r[0] for r in rows)
    finally:
        con.close()


def verify_restore(source: Path, backup: Path, metadata=None) -> list[str]:
    """比较源库与备份副本, 返回差异列表(空 = 一致)。不修改文件。"""
    differences: list[str] = []

    src_integrity = _integrity(source)
    dst_integrity = _integrity(backup)
    if src_integrity != "ok":
        differences.append(f"源库 integrity_check: {src_integrity}")
    if dst_integrity != "ok":
        differences.append(f"备份副本 integrity_check: {dst_integrity}")

    src_tables = _tables(source)
    dst_tables = _tables(backup)
    if src_tables != dst_tables:
        missing = src_tables - dst_tables
        extra = dst_tables - src_tables
        if missing:
            differences.append(f"备份副本缺失表: {sorted(missing)}")
        if extra:
            differences.append(f"备份副本多余表: {sorted(extra)}")

    common = src_tables & dst_tables
    src_counts = _row_counts(source, common)
    dst_counts = _row_counts(backup, common)
    for t in sorted(common):
        if src_counts[t] != dst_counts[t]:
            differences.append(f"表 {t}: 行数不一致(源 {src_counts[t]} vs 备份 {dst_counts[t]})")

    src_pk = _pk_sets(source, common)
    dst_pk = _pk_sets(backup, common)
    for t in sorted(common):
        if src_pk[t] != dst_pk[t]:
            differences.append(f"表 {t}: 主键集合不一致(源 {sorted(src_pk[t])} vs 备份 {sorted(dst_pk[t])})")

    if metadata is not None:
        from scripts.check_db_baseline import compare_schema

        for item in compare_schema(f"sqlite:///{backup.as_posix()}", metadata):
            differences.append(f"备份副本结构: {item}")

    return sorted(differences)


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 SQLite 备份恢复副本与源库一致")
    parser.add_argument("--source", required=True, help="源库(URL 或路径)")
    parser.add_argument("--backup", required=True, help="备份副本(URL 或路径)")
    args = parser.parse_args()

    from backend.models.database import Base
    from backend.models import app_setting, data_status, valuation  # noqa: F401

    differences = verify_restore(_path(args.source), _path(args.backup), Base.metadata)
    for item in differences:
        print(item)
    if differences:
        print(f"共 {len(differences)} 处差异: 备份副本不可依赖")
        return 1
    print("✅ 恢复副本验证通过(integrity/表集合/行数/主键/结构全部一致)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
