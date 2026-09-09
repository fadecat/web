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
import struct
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


def _encode_cell(value) -> bytes:
    """把单个单元格编码为自描述帧: 1 字节类型标签 + 8 字节长度(>Q) + 完整 payload。

    None/int/float/str/bytes 分开编码; str 用 UTF-8, float 用 struct.pack('>d')。
    长度前缀使任何控制字符 / NUL 都无法改变分帧, 类型标签使 "1"(TEXT) 与 1(INTEGER)
    产生不同帧(修复旧实现用 quote() 在 NUL 处截断与类型混淆的漏检)。
    """
    if value is None:
        return b"N" + struct.pack(">Q", 0)
    if isinstance(value, int):
        payload = str(value).encode("utf-8")
        return b"I" + struct.pack(">Q", len(payload)) + payload
    if isinstance(value, float):
        return b"F" + struct.pack(">Q", 8) + struct.pack(">d", value)
    if isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
        return b"B" + struct.pack(">Q", len(payload)) + payload
    if isinstance(value, str):
        payload = value.encode("utf-8")
        return b"S" + struct.pack(">Q", len(payload)) + payload
    # 兜底: 理论上 sqlite3 不会返回上述之外的类型, 仍按文本 framed 编码避免异常
    payload = repr(value).encode("utf-8")
    return b"S" + struct.pack(">Q", len(payload)) + payload


def _table_digest(con, table: str) -> str:
    """确定性全行内容摘要: 每列按 类型标签 + 固定宽度长度 + 完整 payload 帧编码。

    帧自描述(类型标签 + 长度 + payload), 列与行都带长度边界, NUL / 分隔符 \\x1f \\x1e
    都无法破坏分帧或制造碰撞; 行间以 \\x1e 分隔。
    """
    columns = [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
    pk_columns = _pk_columns(con, table)
    order = ", ".join(f'"{column}"' for column in (pk_columns or columns))
    col_list = ", ".join(f'"{column}"' for column in columns)
    digest = hashlib.sha256()
    for row in con.execute(f'SELECT {col_list} FROM "{table}" ORDER BY {order}'):
        for cell in row:
            digest.update(_encode_cell(cell))
        digest.update(b"\x1e")  # 行边界
    return digest.hexdigest()


def _table_digests(db_path: Path, tables: set[str]) -> dict[str, str]:
    with contextlib.closing(readonly_connection(db_path)) as con:
        return {t: _table_digest(con, t) for t in tables}


def _integrity(db_path: Path) -> str:
    with contextlib.closing(readonly_connection(db_path)) as con:
        rows = con.execute("PRAGMA integrity_check").fetchall()
        return ", ".join(r[0] for r in rows)


def _migration_revision(db_path: Path) -> "str | None":
    """读取库的 Alembic revision。

    - alembic_version 表不存在 → None(未版本化)
    - 存在但零行 / 多行 / 空字符串 / 非字符串 → 抛 ValueError(明确错误, 不吞异常)
    """
    with contextlib.closing(readonly_connection(db_path)) as con:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "alembic_version" not in tables:
            return None
        rows = con.execute("SELECT version_num FROM alembic_version").fetchall()
        if len(rows) == 0:
            raise ValueError("alembic_version 表存在但为零行, 无法确定 migration revision")
        if len(rows) > 1:
            raise ValueError(f"alembic_version 表存在 {len(rows)} 行, revision 不唯一")
        value = rows[0][0]
        if value is None or (isinstance(value, str) and value.strip() == ""):
            raise ValueError("alembic_version 的 revision 为空字符串")
        return str(value)


def verify_restore(
    source: Path,
    backup: Path,
    metadata=None,
    *,
    expected_backup_revision: "str | None" = None,
) -> list[str]:
    """比较源库与备份副本, 返回差异列表(空 = 一致)。不修改文件。

    alembic_version 是迁移元数据表: 不进入业务表集合/行数/主键值/摘要比较,
    stamp 前后的副本验证都通过(R5-03)。

    迁移 revision 比较(R6-01):
    - 默认(未传 expected_backup_revision): 源与副本 revision 必须完全相同, 任何
      版本差异都被拒绝(包括源无版本表 / 副本已有 revision 的不匹配)。
    - 接管模式(传入 expected_backup_revision): 仅允许"源无 revision 且副本 revision
      等于指定值"。用于"未版本化源库与已 stamp 副本"的第二次验证; 普通备份恢复
      不得使用。
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

    # 迁移 revision 比较(R6-01): 默认严格; 接管模式需显式 expected_backup_revision
    src_rev = _migration_revision(source)
    dst_rev = _migration_revision(backup)
    if expected_backup_revision is not None:
        # 接管模式: 只允许"源无 revision 且副本等于预期"
        if src_rev is not None:
            differences.append(
                f"接管模式要求源库无 migration revision(实际 {src_rev}); "
                f"未版本化源库接管只允许源无版本表"
            )
        if dst_rev != expected_backup_revision:
            differences.append(
                f"副本 migration revision 为 {dst_rev}, 与接管预期 "
                f"{expected_backup_revision} 不一致"
            )
    else:
        # 默认: 源与副本 revision 必须完全相同(拒绝任意版本差异)
        if src_rev != dst_rev:
            differences.append(
                f"migration revision 不一致(源 {src_rev} vs 备份 {dst_rev}); "
                f"默认恢复验证拒绝版本差异"
            )

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
    parser.add_argument(
        "--expected-backup-revision",
        default=None,
        help=(
            "仅用于'未版本化源库与已 stamp 副本'的第二次接管验证: 显式声明副本应有的 "
            "revision(如 0001)。普通备份恢复(源与副本应 revision 完全一致)不得使用此参数, "
            "留空则默认严格比较两侧 revision。"
        ),
    )
    args = parser.parse_args()

    from backend.models.database import Base
    from backend.models import app_setting, data_status, valuation  # noqa: F401

    differences = verify_restore(
        _path(args.source),
        _path(args.backup),
        Base.metadata,
        expected_backup_revision=args.expected_backup_revision,
    )
    for item in differences:
        print(item)
    if differences:
        print(f"共 {len(differences)} 处差异: 备份副本不可依赖")
        return 1
    print("✅ 恢复副本验证通过(integrity/表集合/行数/主键值/内容摘要/结构全部一致)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
