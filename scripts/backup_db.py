# -*- coding: utf-8 -*-
"""SQLite 一致性备份脚本(正确处理 WAL)。

为什么不用文件复制: WAL 模式下主 .db 文件不包含最近写入, 直接 copy 会遗漏
web.db-wal 里的未 checkpoint 数据, 得到损坏/不完整的备份。SQLite 官方
Connection.backup() 会连同 WAL 一并快照, 是唯一可靠的在运行中备份方式。

用法(第四阶段/T7: 显式路径, 默认目标目录只能由显式 source 派生):
    python scripts/backup_db.py --source sqlite:///D:/path/to/web.db
    python scripts/backup_db.py --source D:/path/to/web.db
    python scripts/backup_db.py --list D:/path/to/backups   # 列出已有备份
"""
from __future__ import annotations

import argparse
import os
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
    """用 SQLite backup API 做一致性快照(含 WAL)。

    安全(Task 4 / R6-09): 先用 os.open(O_CREAT | O_EXCL | O_RDWR) 原子占位显式
    dst(立即关闭 fd), 再交给 sqlite3.connect。占位保证目标不存在, 任何 backup 异常
    都关闭连接并删除占位文件, 不留下半成品。删除失败则把原异常与清理异常一并暴露
    (ExceptionGroup)。源库用只读连接。
    """
    src = src.resolve(strict=True)
    if not src.is_file():
        raise ValueError(f"源库不是普通文件: {src}")
    dst = dst.resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 原子占位: dst 已存在时 O_EXCL 直接抛 FileExistsError(拒绝覆盖)
    try:
        fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        raise FileExistsError(f"拒绝覆盖已有备份: {dst}")
    os.close(fd)  # 占位已建立, 立即交还 fd 给 SQLite
    src_con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            with dst_con:
                src_con.backup(dst_con)
        finally:
            dst_con.close()
    except Exception as backup_err:
        # backup 失败: 删除占位文件, 不让半成品进入 --list
        cleanup_err = None
        try:
            dst.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_err = exc
        if cleanup_err is not None:
            raise RuntimeError(
                f"备份失败且无法清理占位文件: {dst}"
            ) from ExceptionGroup(
                "backup_failed_and_cleanup_failed", [backup_err, cleanup_err]
            )
        raise
    finally:
        src_con.close()


def _resolve_source(raw: str) -> Path:
    """接受 sqlite:///... URL 或纯文件路径。"""
    if raw.startswith("sqlite:///"):
        return Path(raw[len("sqlite:///"):])
    return Path(raw)


def do_backup(source: str, destination_dir: Path | None = None) -> int:
    src = _resolve_source(source).resolve()
    if not src.is_file():
        print(f"源库不是普通文件: {src}", file=sys.stderr)
        return 2
    target_dir = (destination_dir or src.parent).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    # 微秒时间戳: 同一秒两次备份不冲突(R5-06)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dst = target_dir / f"{src.stem}.{ts}.db"

    try:
        backup(src, dst)  # 原子占位 + SQLite backup
    except FileExistsError:
        # 占位竞争(极少: 同微秒时间戳或并发), 保守返回非 0
        print(f"备份目标已存在(时间戳冲突或并发写同一目标): {dst}", file=sys.stderr)
        return 2

    src_counts = _table_counts(src)
    dst_counts = _table_counts(dst)
    mismatch = [t for t in src_counts if src_counts[t] != dst_counts.get(t)]
    integrity = _integrity(dst)

    if mismatch or integrity != "ok":
        # 校验失败: 隔离为 .failed 后缀, 不进 --list(*.db), 保留供排查
        failed = dst.with_suffix(".failed")
        try:
            dst.rename(failed)
        except OSError:
            pass
        print(
            f"❌ 备份校验失败, 文件已隔离为 {failed.name}, 请勿依赖此备份",
            file=sys.stderr,
        )
        return 1

    # 仅在 integrity、表集合、每表行数全部一致后才发布为可列出备份
    print(f"备份完成: {dst.resolve()} ({dst.stat().st_size:,} 字节)")
    print(f"源库表数: {len(src_counts)} | 备份表数: {len(dst_counts)}")
    print(f"行数校验: {'一致' if not mismatch else '不一致! ' + ', '.join(mismatch)}")
    print(f"integrity_check: {integrity}")
    print("✅ 备份可恢复校验通过")
    return 0


def list_backups(directory: Path) -> int:
    """列出指定目录下的备份文件(Task 4 / R6-08: 失败即停语义)。

    不存在 / 非目录 / 不可读 → 返回 2 并输出 stderr; 仅存在且可读的空目录
    才返回 0 并打印"暂无备份"。
    """
    directory = Path(directory)
    if not directory.exists():
        print(f"备份目录不存在: {directory}", file=sys.stderr)
        return 2
    if not directory.is_dir():
        print(f"路径不是目录: {directory}", file=sys.stderr)
        return 2
    try:
        files = sorted(directory.glob("*.db"))
    except OSError as exc:
        print(f"无法读取备份目录: {directory}: {exc}", file=sys.stderr)
        return 2
    if not files:
        print("暂无备份")
        return 0
    for f in files:
        print(f"{f.name}  {f.stat().st_size:,} 字节  {datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d %H:%M:%S}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite 一致性备份(含 WAL)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", help="源库(sqlite:///... URL 或文件路径)")
    mode.add_argument("--list", metavar="DIRECTORY", help="列出指定目录的备份")
    parser.add_argument("--destination-dir", help="备份目录(默认: 源库所在目录)")
    args = parser.parse_args()
    if args.list:
        if args.destination_dir:
            parser.error("--list 不能与 --destination-dir 同时使用")
        return list_backups(Path(args.list))
    return do_backup(args.source, Path(args.destination_dir) if args.destination_dir else None)


if __name__ == "__main__":
    sys.exit(main())
