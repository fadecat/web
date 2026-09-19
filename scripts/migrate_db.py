# -*- coding: utf-8 -*-
"""把指定 SQLite 库迁移到 alembic head(组合实验室/P0~P2 的 0005~0007 迁移)。

为什么需要它: 直接 `python -m alembic -x database_url=... upgrade head` **必须在仓库根目录执行**
(alembic 靠 CWD 找 `alembic.ini`, 且 ini 里 `prepend_sys_path = .` 也依赖 CWD)。
在别的目录跑会报 `FAILED: No 'script_location' key found in configuration.` 或
`ModuleNotFoundError: No module named 'backend'`。
本脚本以 `__file__` 反推仓库根, 因此**从任何目录都能跑**。

用法:
    python scripts/migrate_db.py                  # 默认 data/web.db
    python scripts/migrate_db.py <db路径>          # 指定库(本地临时库 / ECS 的 /opt/webapp/data/web.db)
    python scripts/migrate_db.py --status         # 只看版本, 什么都不改

安全约定(docs/database-migration-runbook.md):
- 日常回退用**备份恢复**, 不用 `alembic downgrade`;
- 生产库(ECS)升级前务必先 `python scripts/backup_db.py`;
- 本脚本只做 `upgrade head`(纯前进), 不提供 downgrade。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _current_version(db_path: Path) -> str:
    if not db_path.exists():
        return "<库不存在>"
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute("select name from sqlite_master where type='table'")
        }
        if "alembic_version" not in tables:
            return "<无 alembic_version 表>"
        row = conn.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else "<空>"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 SQLite 库迁移到 alembic head")
    parser.add_argument("db", nargs="?", default="data/web.db", help="库路径(默认 data/web.db)")
    parser.add_argument("--status", action="store_true", help="只打印当前版本, 不做修改")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()

    before = _current_version(db_path)
    print(f"库: {db_path}")
    print(f"迁移前版本: {before}")
    if args.status:
        return 0

    if not db_path.exists():
        print("库不存在; 先创建空库或从备份/ECS 复制一份再跑。", file=sys.stderr)
        return 2

    # 让 `backend` 可导入(prepend_sys_path 依赖 CWD, 这里显式补齐)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    # sqlite URL 用 posix 风格绝对路径(Windows 反斜杠在 URL 里会被吃掉)
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(cfg, "head")

    after = _current_version(db_path)
    print(f"迁移后版本: {after}")
    print("OK" if after == "0007" or after != before else "版本未变化(可能已是 head)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
