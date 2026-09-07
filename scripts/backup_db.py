# -*- coding: utf-8 -*-
"""SQLite 一致性备份脚本(正确处理 WAL)。

为什么不用文件复制: WAL 模式下主 .db 文件不包含最近写入, 直接 copy 会遗漏
web.db-wal 里的未 checkpoint 数据, 得到损坏/不完整的备份。SQLite 官方
Connection.backup() 会连同 WAL 一并快照, 是唯一可靠的在运行中备份方式。

用法:
    python scripts/backup_db.py            # 备份到 data/backups/ 并校验
    python scripts/backup_db.py --list     # 列出已有备份
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DB = PROJECT_ROOT / "data" / "web.db"
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"


def _table_counts(db_path: Path) -> dict[str, int]:
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {t: cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()


def _integrity(db_path: Path) -> str:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("PRAGMA integrity_check").fetchall()
        return ", ".join(r[0] for r in rows)
    finally:
        con.close()


def backup(src: Path, dst: Path) -> None:
    """用 SQLite backup API 做一致性快照(含 WAL)。"""
    src_con = sqlite3.connect(src)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            with dst_con:
                src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()


def do_backup() -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"web.db.{ts}.db"
    backup(SRC_DB, dst)

    src_counts = _table_counts(SRC_DB)
    dst_counts = _table_counts(dst)
    mismatch = [t for t in src_counts if src_counts[t] != dst_counts.get(t)]
    integrity = _integrity(dst)

    print(f"备份完成: {dst} ({dst.stat().st_size:,} 字节)")
    print(f"源库表数: {len(src_counts)} | 备份表数: {len(dst_counts)}")
    print(f"行数校验: {'一致' if not mismatch else '不一致! ' + ', '.join(mismatch)}")
    print(f"integrity_check: {integrity}")
    if mismatch or integrity != "ok":
        print("❌ 备份校验失败, 请勿依赖此备份", file=sys.stderr)
        return 1
    print("✅ 备份可恢复校验通过")
    return 0


def list_backups() -> int:
    if not BACKUP_DIR.exists():
        print("暂无备份")
        return 0
    files = sorted(BACKUP_DIR.glob("web.db.*.db"))
    if not files:
        print("暂无备份")
        return 0
    for f in files:
        print(f"{f.name}  {f.stat().st_size:,} 字节  {datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d %H:%M:%S}")
    return 0


if __name__ == "__main__":
    if "--list" in sys.argv:
        sys.exit(list_backups())
    sys.exit(do_backup())
