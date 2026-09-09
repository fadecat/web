# -*- coding: utf-8 -*-
"""SQLite 一致性备份脚本(正确处理 WAL)。

为什么不用文件复制: WAL 模式下主 .db 文件不包含最近写入, 直接 copy 会遗漏
web.db-wal 里的未 checkpoint 数据, 得到损坏/不完整的备份。SQLite 官方
Connection.backup() 会连同 WAL 一并快照, 是唯一可靠的在运行中备份方式。

用法(第四阶段/T7: 显式路径, 默认目标目录只能由显式 source 派生):
    python scripts/backup_db.py --source sqlite:///D:/path/to/web.db
    python scripts/backup_db.py --source D:/path/to/web.db
    python scripts/backup_db.py --list     # 列出已有备份
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "backups"


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


def _resolve_source(raw: str) -> Path:
    """接受 sqlite:///... URL 或纯文件路径。"""
    if raw.startswith("sqlite:///"):
        return Path(raw[len("sqlite:///"):])
    return Path(raw)


def do_backup(source: str, destination_dir: Path | None = None) -> int:
    src = _resolve_source(source).resolve()
    if not src.exists():
        print(f"源库不存在: {src}", file=sys.stderr)
        return 2
    target_dir = (destination_dir or src.parent).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = target_dir / f"{src.stem}.{ts}.db"

    if dst.exists():
        raise FileExistsError(f"拒绝覆盖已有备份: {dst}")

    backup(src, dst)

    src_counts = _table_counts(src)
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
    files = sorted(BACKUP_DIR.glob("*.db"))
    if not files:
        print("暂无备份")
        return 0
    for f in files:
        print(f"{f.name}  {f.stat().st_size:,} 字节  {datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d %H:%M:%S}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite 一致性备份(含 WAL)")
    parser.add_argument("--source", required=True, help="源库(sqlite:///... URL 或文件路径)")
    parser.add_argument("--destination-dir", help="备份目录(默认: 源库所在目录)")
    parser.add_argument("--list", action="store_true", help="列出已有备份")
    args = parser.parse_args()
    if args.list:
        return list_backups()
    return do_backup(args.source, Path(args.destination_dir) if args.destination_dir else None)


if __name__ == "__main__":
    sys.exit(main())
