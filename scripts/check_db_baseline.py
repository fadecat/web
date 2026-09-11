# -*- coding: utf-8 -*-
"""已有 SQLite 库与 ORM 结构的只读比对(第四阶段/T6, 第五阶段/T2 安全收口).

- 严格只读: 用 sqlite_readonly.readonly_engine(`?mode=ro` URI), 不存在路径
  直接报错(R5-01: 绝不创建空库), 全部 Inspector 调用在 dispose 前完成并释放句柄。
- 比较: 业务表集合(排除 Alembic 元数据表 alembic_version)、列名、SQLite
  affinity、nullable、主键列集合、唯一约束列集合、显式索引名及列顺序。
- 差异稳定排序, 空列表 = 匹配。
- 使用: `python scripts/check_db_baseline.py --database-url sqlite:///绝对路径`
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import inspect

from scripts.sqlite_readonly import readonly_engine

# Alembic 迁移元数据表: 与业务表分开处理, 不作为 ORM 漂移差异(R5-03)
MIGRATION_TABLES = {"alembic_version"}


def _affinity(declared_type: object) -> str:
    """按 SQLite affinity 规则归一化列类型(比较用)。"""
    t = str(declared_type).upper()
    if "INT" in t:
        return "INTEGER"
    if "CHAR" in t or "CLOB" in t or "TEXT" in t:
        return "TEXT"
    if "BLOB" in t or not t:
        return "BLOB"
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return "REAL"
    return "NUMERIC"


def compare_schema(url: str, metadata) -> list[str]:
    """比较目标 SQLite 与 metadata 定义的结构, 返回差异列表(稳定排序)。

    只读: engine 是 NullPool + mode=ro; 全部 inspector 调用在 try 内,
    finally 才 dispose(R5-01: 不在 dispose 后继续用 inspector)。
    """
    differences: list[str] = []

    engine = readonly_engine(url)
    try:
        inspector = inspect(engine)
        db_tables = set(inspector.get_table_names()) - MIGRATION_TABLES
        model_tables = set(metadata.tables.keys()) - MIGRATION_TABLES

        for t in sorted(db_tables - model_tables):
            differences.append(f"表 {t}: 数据库中多余(ORM 未定义)")
        for t in sorted(model_tables - db_tables):
            differences.append(f"表 {t}: 数据库缺失(ORM 已定义)")

        for t in sorted(model_tables & db_tables):
            db_cols = {c["name"]: c for c in inspector.get_columns(t)}
            model_cols = metadata.tables[t].columns
            model_names = set(model_cols.keys())

            for c in sorted(model_names - set(db_cols.keys())):
                differences.append(f"表 {t}: 缺列 {c}")
            for c in sorted(set(db_cols.keys()) - model_names):
                differences.append(f"表 {t}: 多余列 {c}")

            for c in sorted(model_names & set(db_cols.keys())):
                db_col = db_cols[c]
                model_col = model_cols[c]
                db_aff = _affinity(db_col.get("type"))
                model_aff = _affinity(model_col.type)
                if db_aff != model_aff:
                    differences.append(
                        f"表 {t}: 列 {c} 类型不匹配(库 {db_aff} vs ORM {model_aff})"
                    )
                if db_col.get("nullable") != model_col.nullable:
                    differences.append(
                        f"表 {t}: 列 {c} 可空性不匹配(库 {db_col.get('nullable')} vs ORM {model_col.nullable})"
                    )

            db_pk = set(inspector.get_pk_constraint(t).get("constrained_columns") or [])
            model_pk = {c.name for c in model_cols if c.primary_key}
            if db_pk != model_pk:
                differences.append(f"表 {t}: 主键列集合不匹配(库 {sorted(db_pk)} vs ORM {sorted(model_pk)})")

            db_uq = {tuple(sorted(u["column_names"])) for u in inspector.get_unique_constraints(t)}
            model_uq = {
                tuple(sorted([c.name for c in u.columns]))
                for u in metadata.tables[t].constraints
                if hasattr(u, "columns") and getattr(u, "name", None) is not None
            }
            if db_uq != model_uq:
                differences.append(
                    f"表 {t}: 唯一约束列集合不匹配(库 {sorted(db_uq)} vs ORM {sorted(model_uq)})"
                )

            db_idx = {
                (i["name"], tuple(i["column_names"]))
                for i in inspector.get_indexes(t)
                if i["name"]
            }
            model_idx = {
                (ix.name, tuple(c.name for c in ix.columns))
                for ix in metadata.tables[t].indexes
            }
            if db_idx != model_idx:
                differences.append(
                    f"表 {t}: 索引集合不匹配(库 {sorted(db_idx)} vs ORM {sorted(model_idx)})"
                )
    finally:
        engine.dispose()

    return sorted(differences)


def main() -> int:
    parser = argparse.ArgumentParser(description="只读比较 SQLite 库与 ORM 结构")
    parser.add_argument("--database-url", required=True, help="sqlite:///绝对路径")
    args = parser.parse_args()
    if not args.database_url.startswith("sqlite:///"):
        parser.error("本阶段只允许显式 SQLite URL")
    from backend.models.database import Base
    from backend.models import app_setting, data_status, jisilu_stock, valuation  # noqa: F401

    differences = compare_schema(args.database_url, Base.metadata)
    for difference in differences:
        print(difference)
    if differences:
        print(f"共 {len(differences)} 处结构差异: 禁止 stamp/upgrade, 先对齐结构或走备份恢复")
        return 1
    print("结构匹配(只读核对通过), 可显式 stamp 0001")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
