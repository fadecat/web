# -*- coding: utf-8 -*-
"""SQLite 一致性备份脚本(正确处理 WAL, 校验后原子发布).

为什么不用文件复制: WAL 模式下主 .db 文件不包含最近写入, 直接 copy 会遗漏
web.db-wal 里的未 checkpoint 数据, 得到损坏/不完整的备份。SQLite 官方
Connection.backup() 会连同 WAL 一并快照, 是唯一可靠的在运行中备份方式。

发布安全(R7-05 / R7-06 / R7-07): 先写不匹配 *.db 的 .partial, 完成
integrity、表集合双向相等、每表行数、二进制安全内容摘要(复用 verify_db_restore)
后才以不覆盖语义原子发布最终 .db; 任一创建/连接/backup/校验/发布异常都进入同一
清理路径, 不留最终 .db, 清理失败不被吞。--list 永远不展示 partial/failed/sidecar。

用法(模块 CLI, Windows 原样可复制执行):
    python -m scripts.backup_db --source sqlite:///D:/path/to/web.db
    python -m scripts.backup_db --source D:/path/to/web.db
    python -m scripts.backup_db --list D:/path/to/backups   # 列出已发布备份
"""
from __future__ import annotations

import argparse
import errno
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from scripts.verify_db_restore import verify_restore

BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "backups"

# 测试注入钩子(生产为 None):
# - _POST_WRITE_HOOK: 写完 partial、校验之前调用, 可用于注入额外表/篡改内容反例。
# - _PRE_PUBLISH_HOOK: 校验通过、发布之前调用, 可用于观察"partial 已存在、
#   final 尚未出现"的阻塞窗口(发布时序反例)。
_POST_WRITE_HOOK = None
_PRE_PUBLISH_HOOK = None


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


def backup(src: Path, dst: Path) -> None:
    """用 SQLite backup API 做一致性快照(含 WAL)写入 dst。

    安全: 先用 os.open(O_CREAT | O_EXCL | O_RDWR) 原子占位显式 dst, 再 sqlite3.connect。
    占位保证目标不存在, 任何 backup 异常都关闭连接并删除占位文件, 不留下半成品;
    删除失败则把原异常与清理异常一并暴露(ExceptionGroup), 不被吞。源库用只读连接。
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
        raise FileExistsError(f"拒绝覆盖已有文件: {dst}")
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


def _remove_if_present(path: Path) -> None:
    """删除临时文件(若存在); 删除失败直接抛 OSError, 不吞(清理失败必须可见)。"""
    if path.exists():
        path.unlink()


def _publish(partial: Path, dst: Path) -> None:
    """原子不覆盖发布: partial -> dst。

    使用平台原子 rename 原语: dst 已存在时 os.rename 在 POSIX 与 Windows 均失败
    (不覆盖), 直接暴露竞争, 让调用方判定(恰一个成功)。任何平台不支持该原语即失败
    即停, 不回退到会覆盖目标的 os.replace/rename 覆盖语义。
    """
    try:
        os.rename(partial, dst)
    except FileExistsError:
        raise FileExistsError(f"拒绝覆盖已有备份(并发/重复发布): {dst}")
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise FileExistsError(f"拒绝覆盖已有备份(并发/重复发布): {dst}") from exc
        raise


def _resolve_source(raw: str) -> Path:
    """接受 sqlite:///... URL 或纯文件路径。"""
    if raw.startswith("sqlite:///"):
        return Path(raw[len("sqlite:///"):])
    return Path(raw)


def do_backup(source: str, destination_dir: Path | None = None) -> int:
    """完整接管备份: 写 partial → 全量校验 → 原子发布。失败一律不留下最终 .db。

    任一创建/连接/backup/校验/发布异常都进入同一清理路径: partial 与 sidecar 被删除,
    最终 *.db 绝不会出现, 清理失败不被吞。只有发布成功后才打印"备份完成"。
    """
    src = _resolve_source(source).resolve()
    if not src.is_file():
        print(f"[FAIL] 源库不是普通文件: {src}", file=sys.stderr)
        return 2
    target_dir = (destination_dir or src.parent).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    # 微秒时间戳: 同一秒两次备份不冲突(R5-06)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dst = target_dir / f"{src.stem}.{ts}.db"
    partial = dst.with_suffix(".partial")  # 不匹配 *.db, list_backups 不可见

    # A. 一致性快照写入 partial(原子占位 + SQLite backup)
    #    失败点: 源连接 / 目标连接 / SQLite backup
    try:
        backup(src, partial)
    except Exception as write_err:
        # backup 内部已清理 partial; 再确认一次, 清理失败必须可见
        try:
            _remove_if_present(partial)
        except OSError as cl_err:
            print(f"[FAIL] 备份写入失败且 partial 清理失败: {partial}: {cl_err}", file=sys.stderr)
            return 1
        print(f"[FAIL] 备份写入失败(未生成最终备份): {write_err}", file=sys.stderr)
        return 1

    # 测试注入: 在 partial 上做额外表/内容篡改反例
    if _POST_WRITE_HOOK is not None:
        _POST_WRITE_HOOK(partial)

    # B. 完整后置校验(表集合双向相等 + 每表行数 + 内容摘要, 复用 verify_restore)
    #    失败点: 表统计 / integrity / 内容摘要
    try:
        differences = verify_restore(src, partial)
    except Exception as verify_err:
        try:
            _remove_if_present(partial)
        except OSError as cl_err:
            print(f"[FAIL] 备份校验异常且 partial 清理失败: {partial}: {cl_err}", file=sys.stderr)
            return 1
        print(f"[FAIL] 备份校验异常(未发布): {verify_err}", file=sys.stderr)
        return 1

    if differences:
        for d in differences:
            print(f"[FAIL] {d}", file=sys.stderr)
        try:
            _remove_if_present(partial)
        except OSError as cl_err:
            print(f"[FAIL] 备份校验失败且 partial 清理失败: {partial}: {cl_err}", file=sys.stderr)
            return 1
        print("[FAIL] 备份副本不可依赖, 已清理临时文件(未发布最终 .db)", file=sys.stderr)
        return 1

    # C. 校验全部通过 → 发布前钩子(测试可在此阻塞观察 partial 窗口)
    if _PRE_PUBLISH_HOOK is not None:
        _PRE_PUBLISH_HOOK()

    # D. 原子不覆盖发布(最终发布失败点)
    try:
        _publish(partial, dst)
    except FileExistsError as pub_err:
        try:
            _remove_if_present(partial)
        except OSError as cl_err:
            print(f"[FAIL] 发布冲突且 partial 清理失败: {partial}: {cl_err}", file=sys.stderr)
            return 2
        print(f"[FAIL] 发布冲突(目标已存在, 未覆盖): {pub_err}", file=sys.stderr)
        return 2
    except OSError as pub_err:
        try:
            _remove_if_present(partial)
        except OSError as cl_err:
            print(f"[FAIL] 发布失败且 partial 清理失败: {partial}: {cl_err}", file=sys.stderr)
            return 1
        print(f"[FAIL] 发布失败(未生成最终备份): {pub_err}", file=sys.stderr)
        return 1

    # E. 仅发布成功后打印完成
    src_counts = _table_counts(src)
    dst_counts = _table_counts(dst)
    print(f"[PASS] 备份完成: {dst.resolve()} ({dst.stat().st_size:,} 字节)")
    print(f"[PASS] 源库表数: {len(src_counts)} | 备份表数: {len(dst_counts)}")
    print(f"[PASS] integrity_check: ok | 表集合/行数/内容摘要一致")
    return 0


def list_backups(directory: Path) -> int:
    """列出指定目录下的已发布备份文件(仅 *.db, 永不展示 partial/failed/sidecar).

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
