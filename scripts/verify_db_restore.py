# -*- coding: utf-8 -*-
"""恢复副本验证脚本(第四阶段/T7, 第五阶段/T3 内容级收口).

接收源库与备份副本, 执行:
- 两边 PRAGMA integrity_check
- 业务表集合一致(Alembic 元数据表 alembic_version 不参与)
- 每表行数一致
- 每表主键值集合一致(R5-02: 比较每行主键值, 而非主键列定义)
- 每表全行内容摘要一致(R5-02: 检测非主键内容篡改)
- 备份副本结构(compare_schema)与 ORM 匹配

任何差异打印 `表名 + 差异类型` 并返回 1; 全部只读连接, 不修改任一文件。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import sys
from pathlib import Path

from scripts.check_db_baseline import MIGRATION_TABLES
from scripts.sqlite_readonly import readonly_connection


def _path(raw: str) -> Path:
    return Path(raw[len("sqlite:///"):]) if raw.startswith("sqlite:///") else Path(raw)


def _tables(db_path: Path) -> set[str]:
    with contextlib.closing(readonly_connection(db_path)) as con:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {r[0] for r in rows}


def _row_counts(db_path: Path, tables: set[str]) -> dict[str, int]:
    with contextlib.closing(readonly_connection(db_path)) as con:
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def _pk_columns(con, table: str) -> list[str]:
    """按 PRAGMA table_info 的 pk 序号排序的主键列名(复合主键保持顺序)。"""
    info = con.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5] > 0]


def _primary_key_values(db_path: Path, tables: set[str]) -> dict[str, set[tuple]]:
    """每张业务表的主键值集合(R5-02: 真实记录主键值, 非列定义)。

    业务表必须显式主键; 无主键表直接报告(无法证明记录集合一致)。
    """
    result: dict[str, set[tuple]] = {}
    with contextlib.closing(readonly_connection(db_path)) as con:
        for table in sorted(tables):
            columns = _pk_columns(con, table)
            if not columns:
                result[table] = set()  # 由调用方报告"无主键"
                continue
            select = ", ".join(f'"{column}"' for column in columns)
            result[table] = set(con.execute(f'SELECT {select} FROM "{table}"').fetchall())
    return result


def _table_digest(con, table: str) -> str:
    """确定性全行内容摘要: 每列 quote() 输出, 按主键(或全部列)排序。"""
    columns = [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
    quoted = ", ".join(f'quote("{column}")' for column in columns)
    pk_columns = _pk_columns(con, table)
    order = ", ".join(f'"{column}"' for column in (pk_columns or columns))
    digest = hashlib.sha256()
    for row in con.execute(f'SELECT {quoted} FROM "{table}" ORDER BY {order}'):
        digest.update("\x1f".join("NULL" if value is None else str(value) for value in row).encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def _table_digests(db_path: Path, tables: set[str]) -> dict[str, str]:
    with contextlib.closing(readonly_connection(db_path)) as con:
        return {t: _table_digest(con, t) for t in tables}


def _integrity(db_path: Path) -> str:
    with contextlib.closing(readonly_connection(db_path)) as con:
        rows = con.execute("PRAGMA integrity_check").fetchall()
        return ", ".join(r[0] for r in rows)


def verify_restore(source: Path, backup: Path, metadata=None) -> list[str]:
    """比较源库与备份副本, 返回差异列表(空 = 一致)。不修改文件。

    alembic_version 是迁移元数据表: 不进入业务表集合/行数/主键值/摘要比较,
    stamp 前后的副本验证都通过(R5-03)。
    """
    differences: list[str] = []

    src_integrity = _integrity(source)
    dst_integrity = _integrity(backup)
    if src_integrity != "ok":
        differences.append(f"源库 integrity_check: {src_integrity}")
    if dst_integrity != "ok":
        differences.append(f"备份副本 integrity_check: {dst_integrity}")

    src_tables = _tables(source) - MIGRATION_TABLES
    dst_tables = _tables(backup) - MIGRATION_TABLES
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

    src_pk = _primary_key_values(source, common)
    dst_pk = _primary_key_values(backup, common)
    for t in sorted(common):
        # 基于主键列定义判断(空表有主键列但 0 行不属于"无主键")
        src_has_pk = _pk_columns_any(source, t)
        dst_has_pk = _pk_columns_any(backup, t)
        if not src_has_pk or not dst_has_pk:
            differences.append(f"表 {t}: 无主键, 无法证明记录集合一致")
            continue
        if src_pk[t] != dst_pk[t]:
            differences.append(
                f"表 {t}: 主键值集合不一致(源 {sorted(src_pk[t])[:3]}... vs 备份 {sorted(dst_pk[t])[:3]}...)"
            )

    src_digests = _table_digests(source, common)
    dst_digests = _table_digests(backup, common)
    for t in sorted(common):
        if src_digests[t] != dst_digests[t]:
            differences.append(f"表 {t}: 内容摘要不一致(非主键数据被篡改)")

    if metadata is not None:
        from scripts.check_db_baseline import compare_schema

        for item in compare_schema(f"sqlite:///{backup.as_posix()}", metadata):
            differences.append(f"备份副本结构: {item}")

    return sorted(differences)


def _pk_columns_any(db_path: Path, table: str) -> bool:
    """辅助: 表是否有主键列(区分"表无主键"与"有主键但值为空表")。"""
    with contextlib.closing(readonly_connection(db_path)) as con:
        return bool(_pk_columns(con, table))


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
    print("✅ 恢复副本验证通过(integrity/表集合/行数/主键值/内容摘要/结构全部一致)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
