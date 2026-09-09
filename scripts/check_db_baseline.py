# -*- coding: utf-8 -*-
"""已有 SQLite 库与 ORM 结构的只读比对(第四阶段/T6).

- 只读: 不执行 DDL、不自动 stamp、不调用 init_db()
- 比较: 业务表集合、列名、SQLite affinity、nullable、主键列集合、
  唯一约束列集合、显式索引名及列顺序
- 差异稳定排序, 空列表 = 匹配
- 使用方式: `python scripts/check_db_baseline.py --database-url sqlite:///绝对路径`
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, inspect


def _sqlite_path(url: str) -> Path:
    """从 sqlite:///... URL 提取文件路径。"""
    raw = url[len("sqlite:///"):]
    return Path(raw)


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
    """比较目标 SQLite 与 metadata 定义的结构, 返回差异列表(稳定排序)。"""
    differences: list[str] = []

    if not url.startswith("sqlite:///"):
        raise ValueError(f"仅支持 SQLite URL: {url}")

    path = _sqlite_path(url)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        inspector = inspect(engine)
    finally:
        engine.dispose()  # 释放连接, 避免占用 .db 文件

    db_tables = set(inspector.get_table_names())
    model_tables = set(metadata.tables.keys())

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
            # 列类型 affinity 比较(可空性/主键单列单独报)
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

        # 主键列集合
        db_pk = set(inspector.get_pk_constraint(t).get("constrained_columns") or [])
        model_pk = {c.name for c in model_cols if c.primary_key}
        if db_pk != model_pk:
            differences.append(f"表 {t}: 主键列集合不匹配(库 {sorted(db_pk)} vs ORM {sorted(model_pk)})")

        # 唯一约束列集合
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

        # 显式索引名及列顺序
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

    return sorted(differences)


def main() -> int:
    parser = argparse.ArgumentParser(description="只读比较 SQLite 库与 ORM 结构")
    parser.add_argument("--database-url", required=True, help="sqlite:///绝对路径")
    args = parser.parse_args()
    if not args.database_url.startswith("sqlite:///"):
        parser.error("本阶段只允许显式 SQLite URL")
    from backend.models.database import Base
    from backend.models import app_setting, data_status, valuation  # noqa: F401

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
