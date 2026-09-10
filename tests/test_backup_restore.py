# -*- coding: utf-8 -*-
"""SQLite 备份与恢复副本验证测试(第四阶段/T7, 第五阶段/T3-T4 收口).

全部在测试临时目录执行, 不读取/复制日常库。
覆盖: WAL 提交行被备份捕获 / 拒绝覆盖已有目标 / 篡改副本被拒 /
结构漂移被拒 / 主键值替换被拒 / 非键内容篡改被拒 / 一致副本通过。
所有 sqlite3 连接用 contextlib.closing 显式关闭(R5-05: 不留句柄)。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing

import pytest

from scripts import backup_db
from scripts.backup_db import backup, do_backup, list_backups, _publish
from scripts.verify_db_restore import verify_restore


def _make_source(db_path, rows: int = 3) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        with conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            for i in range(rows):
                conn.execute("INSERT INTO sample(value) VALUES (?)", (f"row-{i}",))


class TestBackup:
    def test_backup_includes_committed_wal_rows(self, test_artifact_dir):
        source = test_artifact_dir / "source.db"
        destination = test_artifact_dir / "backup.db"
        with closing(sqlite3.connect(source)) as conn:
            with conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("INSERT INTO sample(value) VALUES ('committed-in-wal')")
            # 事务已提交(WAL 已落), 再执行备份
            backup(source, destination)
        with closing(sqlite3.connect(destination)) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert conn.execute("SELECT value FROM sample").fetchone() == ("committed-in-wal",)

    def test_backup_refuses_existing_destination(self, test_artifact_dir):
        """R5-06: 覆盖保护由 backup API 本身保证(测试直接调用生产入口)。"""
        source = test_artifact_dir / "src.db"
        destination = test_artifact_dir / "dst.db"
        _make_source(source)
        destination.write_bytes(b"existing")
        with pytest.raises(FileExistsError, match="拒绝覆盖"):
            backup(source, destination)


class TestBackupAtomicPublish:
    """Task 4 / R6-09: 备份必须在全部校验通过后才发布为可列出文件。

    反例覆盖旧实现的已知缺陷:
    - SQLite backup 中途抛错会留下半成品目标文件;
    - 并发写同一 dst 可能都成功(竞争窗口);
    - list_backups 对缺失目录错误地返回成功码 0。
    """

    def test_backup_removes_partial_file_on_failure(self, test_artifact_dir, monkeypatch):
        """backup 在目标创建后抛异常, 最终目标(占位)文件必须不存在。"""
        import sqlite3 as _sqlite3

        source = test_artifact_dir / "src.db"
        destination = test_artifact_dir / "dst.db"
        _make_source(source)

        class _BoomConn(_sqlite3.Connection):
            def backup(self, target, *args, **kwargs):
                raise _sqlite3.OperationalError("injected backup failure")

        def _boom_connect(*args, **kwargs):
            return _BoomConn(*args, **kwargs)

        monkeypatch.setattr(_sqlite3, "connect", _boom_connect)
        with pytest.raises(_sqlite3.OperationalError, match="injected backup failure"):
            backup(source, destination)
        assert not destination.exists(), "backup 失败后不应留下目标/占位文件"
        # 也没有残留临时文件
        leftovers = list(test_artifact_dir.glob("dst*"))
        assert leftovers == [], f"不应有残留临时文件: {leftovers}"

    def test_concurrent_backup_same_dst_only_one_succeeds(self, test_artifact_dir):
        """并发写同一 dst: 恰好一个成功, 另一个必须抛 FileExistsError。"""
        import threading

        source = test_artifact_dir / "src.db"
        destination = test_artifact_dir / "dst.db"
        _make_source(source)

        succeeded = {}
        conflicted = {}

        def worker(tag):
            try:
                backup(source, destination)
                succeeded[tag] = True
            except FileExistsError:
                conflicted[tag] = True

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert len(succeeded) == 1, f"恰好一个成功, 实际: {succeeded}"
        assert len(conflicted) == 1, f"恰好一个抛 FileExistsError, 实际: {conflicted}"

    def test_list_backups_missing_dir_returns_nonzero(self, test_artifact_dir):
        """缺失目录: list_backups 必须返回非零(失败即停), 旧实现错误返回 0。"""
        missing = test_artifact_dir / "does-not-exist"
        from scripts.backup_db import list_backups

        assert list_backups(missing) != 0


class TestRestoreVerifier:
    def test_restore_verifier_accepts_identical_snapshot(self, test_artifact_dir):
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        _make_source(source)
        backup(source, backup_copy)
        assert verify_restore(source, backup_copy) == []

    def test_restore_verifier_detects_missing_rows(self, test_artifact_dir):
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        _make_source(source, rows=3)
        backup(source, backup_copy)
        # 篡改: 删副本一行
        with closing(sqlite3.connect(backup_copy)) as conn:
            conn.execute("DELETE FROM sample WHERE id = 1")
            conn.commit()
        differences = verify_restore(source, backup_copy)
        assert any("行数不一致" in item for item in differences)

    def test_restore_verifier_detects_schema_drift(self, test_artifact_dir):
        """备份副本结构与 ORM 漂移(加列)被 compare_schema 捕获。"""
        from sqlalchemy import create_engine

        from backend.models.database import Base
        from backend.models import app_setting, data_status, valuation  # noqa: F401

        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        # 源库 = ORM 结构(app_setting 单表足够触发 compare_schema)
        engine = create_engine(f"sqlite:///{source.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()
        backup(source, backup_copy)
        # 篡改: 副本加一列
        with closing(sqlite3.connect(backup_copy)) as conn:
            conn.execute("ALTER TABLE app_setting ADD COLUMN extra TEXT")
            conn.commit()
        differences = verify_restore(source, backup_copy, Base.metadata)
        assert any("多余列" in item or "extra" in item for item in differences)

    def test_restore_verifier_detects_pk_drift(self, test_artifact_dir):
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        _make_source(source, rows=2)
        backup(source, backup_copy)
        # 篡改: 副本主键变化(建新表不现实, 直接改 sqlite_master 会破坏 integrity,
        # 改用不同主键的替换表验证 pk_sets 比较路径)
        with closing(sqlite3.connect(backup_copy)) as conn:
            conn.execute("ALTER TABLE sample RENAME TO sample_old")
            conn.execute("CREATE TABLE sample (id INTEGER, value TEXT NOT NULL, PRIMARY KEY (id, value))")
            conn.execute("INSERT INTO sample SELECT id, value FROM sample_old")
            conn.execute("DROP TABLE sample_old")
            conn.commit()
        differences = verify_restore(source, backup_copy)
        assert any("主键值集合不一致" in item for item in differences)

    def test_restore_verifier_detects_replaced_primary_key(self, test_artifact_dir):
        """R5-02: 同结构、同行数、主键值被替换必须被拒。"""
        from contextlib import closing

        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        _make_source(source, rows=2)
        backup(source, backup_copy)
        with closing(sqlite3.connect(backup_copy)) as conn:
            conn.execute("update sample set id = 99 where id = 2")
            conn.commit()
        differences = verify_restore(source, backup_copy)
        assert any("主键值集合不一致" in item for item in differences)

    def test_restore_verifier_detects_changed_non_key_value(self, test_artifact_dir):
        """R5-02: 主键相同但非主键内容被篡改必须被拒。"""
        from contextlib import closing

        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        _make_source(source, rows=2)
        backup(source, backup_copy)
        with closing(sqlite3.connect(backup_copy)) as conn:
            conn.execute("update sample set value = 'tampered' where id = 2")
            conn.commit()
        differences = verify_restore(source, backup_copy)
        assert any("内容摘要不一致" in item for item in differences)


class TestContentDigest:
    """R6-02: 内容摘要必须编码原始类型与完整 payload, 不被 NUL/分隔符/类型变化绕过。

    这些反例针对旧实现(用 SQLite quote() 生成摘要)的已知漏检: quote() 在 NUL 处
    截断文本, 且无法区分 "1"(TEXT) 与 1(INTEGER)、BLOB 末字节变化、含分隔符的多列值。
    新实现按类型标签 + 固定宽度长度 + 完整 payload 帧编码, 任何控制字符与 NUL
    都不能改变分帧。
    """

    def _build(self, db_path, rows):
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, c1, c2)")
            conn.executemany("INSERT INTO t(id, c1, c2) VALUES (?, ?, ?)", rows)
            conn.commit()

    def test_digest_detects_nul_truncation(self, test_artifact_dir):
        """源 'a\\0x' 与副本 'a\\0y': NUL 后文本不同, 旧 quote() 在 NUL 处截断会漏检。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        self._build(source, [(1, "a\x00x", "k")])
        self._build(backup_copy, [(1, "a\x00y", "k")])
        differences = verify_restore(source, backup_copy)
        assert any("内容摘要不一致" in item for item in differences), differences

    def test_digest_detects_type_change(self, test_artifact_dir):
        """TEXT '1' 与 INTEGER 1: 类型变化必须被发现。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        self._build(source, [(1, "1", "k")])  # TEXT "1"
        self._build(backup_copy, [(1, 1, "k")])  # INTEGER 1
        differences = verify_restore(source, backup_copy)
        assert any("内容摘要不一致" in item for item in differences), differences

    def test_digest_detects_blob_tail_change(self, test_artifact_dir):
        """BLOB b'a\\0b' 末字节变化: 二进制内容必须逐字节比较。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        self._build(source, [(1, b"a\x00b", "k")])
        self._build(backup_copy, [(1, b"a\x00c", "k")])
        differences = verify_restore(source, backup_copy)
        assert any("内容摘要不一致" in item for item in differences), differences

    def test_digest_survives_delimiter_collision(self, test_artifact_dir):
        """含分隔符 \\x1f/\\x1e 的多列值, 列顺序不同必须被发现(不能靠列拼接)。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        # ("a","b\x1fc") 与 ("a\x1fb","c") 在 \x1f 连接下文本相同, 但列顺序不同
        self._build(source, [(1, "a", "b\x1fc")])
        self._build(backup_copy, [(1, "a\x1fb", "c")])
        differences = verify_restore(source, backup_copy)
        assert any("内容摘要不一致" in item for item in differences), differences


class TestMigrationRevision:
    """R6-01: 默认严格比较 revision; 接管特例需显式 expected_backup_revision。"""

    def _build(self, db_path, revision):
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "CREATE TABLE app_setting "
                "(key VARCHAR(64) PRIMARY KEY, value VARCHAR(2000), updated_at DATETIME)"
            )
            conn.execute(
                "INSERT INTO app_setting(key, value, updated_at) "
                "VALUES ('k', 'v', '2026-01-01 00:00:00')"
            )
            if revision is not None:
                conn.execute(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
                conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
            conn.commit()

    def test_no_version_table_on_both_sides_passes(self, test_artifact_dir):
        """两侧均无版本表(未版本化): 默认严格比较, revision 同为 None → 通过。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        self._build(source, None)
        self._build(backup_copy, None)
        assert verify_restore(source, backup_copy) == []

    def test_same_revision_on_both_sides_passes(self, test_artifact_dir):
        """两侧均为 0001: 默认严格比较, revision 相同 → 通过。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        self._build(source, "0001")
        self._build(backup_copy, "0001")
        assert verify_restore(source, backup_copy) == []

    def test_different_revision_default_fails(self, test_artifact_dir):
        """源 0001、副本 9999: 默认严格比较必须失败。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        self._build(source, "0001")
        self._build(backup_copy, "9999")
        differences = verify_restore(source, backup_copy)
        assert any(
            "revision" in item or "版本" in item for item in differences
        ), differences

    def test_adoption_with_expected_revision_passes(self, test_artifact_dir):
        """接管特例: 源无版本表、副本 0001, 传入 expected_backup_revision='0001' → 通过。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        self._build(source, None)  # 未版本化源库
        self._build(backup_copy, "0001")  # 已 stamp 副本
        assert verify_restore(source, backup_copy, expected_backup_revision="0001") == []

    def test_adoption_without_expected_revision_fails(self, test_artifact_dir):
        """接管特例未传参: 源 None、副本 0001 默认严格比较必须失败。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "bak.db"
        self._build(source, None)
        self._build(backup_copy, "0001")
        differences = verify_restore(source, backup_copy)
        assert any(
            "revision" in item or "版本" in item for item in differences
        ), differences


class TestBackupCli:
    """R5-06: backup CLI 模式互斥与独立 --list。"""

    def _run_cli(self, args, monkeypatch):
        import scripts.backup_db as bd

        monkeypatch.setattr("sys.argv", ["backup_db.py", *args])
        return bd.main()

    def test_list_mode_works_independently(self, test_artifact_dir, monkeypatch):
        """--list <dir> 不依赖 --source, 返回 0。"""
        assert self._run_cli(["--list", str(test_artifact_dir)], monkeypatch) == 0

    def test_no_mode_is_rejected(self, monkeypatch):
        """无 --source 也无 --list: argparse 返回 2。"""
        import scripts.backup_db as bd
        from unittest.mock import patch

        with patch("sys.argv", ["backup_db.py"]):
            with pytest.raises(SystemExit) as exc:
                bd.main()
        assert exc.value.code == 2

    def test_source_and_list_mutually_exclusive(self, monkeypatch):
        import scripts.backup_db as bd
        from unittest.mock import patch

        with patch("sys.argv", ["backup_db.py", "--source", "x.db", "--list", "dir"]):
            with pytest.raises(SystemExit) as exc:
                bd.main()
        assert exc.value.code == 2

    def test_list_with_destination_dir_rejected(self, test_artifact_dir, monkeypatch):
        import scripts.backup_db as bd
        from unittest.mock import patch

        with patch("sys.argv", ["backup_db.py", "--list", str(test_artifact_dir), "--destination-dir", "other"]):
            with pytest.raises(SystemExit) as exc:
                bd.main()
        assert exc.value.code == 2

    def test_do_backup_generates_unique_filenames(self, test_artifact_dir):
        """同一秒两次备份生成不同文件(微秒时间戳)。"""
        import scripts.backup_db as bd

        src = test_artifact_dir / "src.db"
        _make_source(src, rows=1)
        out = test_artifact_dir / "out"
        out.mkdir()
        assert bd.do_backup(str(src), out) == 0
        assert bd.do_backup(str(src), out) == 0
        files = sorted(out.glob("src.*.db"))
        assert len(files) == 2


class TestPartialPublish:
    """Task 3 / R7-05: 备份先写 .partial, 校验通过后才发布最终 .db。

    覆盖: 发布时序(阻塞期间 list 空、只见 partial)、全部失败点(源连接/目标连接/
    backup/表统计/integrity/内容摘要/发布)、完整比较(额外表/缺表/内容篡改)、
    清理失败不被吞、--list 永不展示 partial/failed/sidecar。
    """

    def test_publish_visibility_blocked_list_empty(self, test_artifact_dir):
        """阻塞发布钩子: 阻塞期间 --list 返回空, 目录只见 .partial; 释放后
        .db 出现且 .partial 消失(R7-05 时序反例)。"""
        source = test_artifact_dir / "src.db"
        _make_source(source)
        out = test_artifact_dir / "out"
        out.mkdir()

        observed: dict = {}
        block = threading.Event()

        def hook():
            observed["partials"] = sorted(p.name for p in out.glob("*.partial"))
            observed["dbs"] = sorted(p.name for p in out.glob("*.db"))
            observed["list_rc"] = list_backups(out)
            block.wait()  # 阻塞直到主线程放行

        backup_db._PRE_PUBLISH_HOOK = hook
        try:
            t = threading.Thread(target=do_backup, args=(str(source), out))
            t.start()
            # 等待钩子完整触发(partial 已写、list 已观测、尚未发布);
            # 必须等 list_rc 就绪, 避免读到赋值中间态的竞态
            for _ in range(500):
                if "list_rc" in observed:
                    break
                time.sleep(0.01)
            assert "list_rc" in observed, "发布前钩子未完整触发"
            # 阻塞窗口: 只见 partial, 无最终 .db, list 为空(返回 0)
            assert observed["dbs"] == [], observed
            assert len(observed["partials"]) == 1, observed
            assert observed["list_rc"] == 0, observed
            block.set()
            t.join(timeout=15)
            assert not t.is_alive(), "do_backup 在释放后仍未结束"
        finally:
            backup_db._PRE_PUBLISH_HOOK = None

        # 发布后: 最终 .db 出现, partial 消失
        dbs = sorted(p.name for p in out.glob("*.db"))
        assert len(dbs) == 1, dbs
        assert not list(out.glob("*.partial")), "发布后 partial 应消失"

    @pytest.mark.parametrize(
        "fail_point",
        [
            "source_connect",
            "dest_connect",
            "backup",
            "table_stats",
            "integrity",
            "content_digest",
            "publish",
        ],
    )
    def test_failure_point_leaves_no_final_db(self, test_artifact_dir, monkeypatch, fail_point):
        """每个失败点: 返回非零、最终 .db 不存在、partial 与 sidecar 已清理。"""
        source = test_artifact_dir / "src.db"
        _make_source(source, rows=2)
        out = test_artifact_dir / "out"
        out.mkdir()

        if fail_point == "source_connect":
            orig = sqlite3.connect

            def _c(*a, **k):
                dsn = str(a[0]) if a else str(k.get("database", ""))
                if "mode=ro" in dsn:
                    raise sqlite3.OperationalError("injected source connect failure")
                return orig(*a, **k)

            monkeypatch.setattr(sqlite3, "connect", _c)
        elif fail_point == "dest_connect":
            orig = sqlite3.connect

            def _c(*a, **k):
                dsn = str(a[0]) if a else str(k.get("database", ""))
                if "mode=ro" not in dsn:
                    raise sqlite3.OperationalError("injected dest connect failure")
                return orig(*a, **k)

            monkeypatch.setattr(sqlite3, "connect", _c)
        elif fail_point == "backup":
            class _BoomConn(sqlite3.Connection):
                def backup(self, target, *args, **kwargs):
                    raise sqlite3.OperationalError("injected backup failure")

            orig = sqlite3.connect

            def _c(*a, **k):
                return _BoomConn(*a, **k)

            monkeypatch.setattr(sqlite3, "connect", _c)
        elif fail_point == "table_stats":
            monkeypatch.setattr(
                backup_db, "verify_restore",
                lambda s, d: ["表 sample: 行数不一致(源 2 vs 备份 0)"],
            )
        elif fail_point == "integrity":
            monkeypatch.setattr(
                backup_db, "verify_restore",
                lambda s, d: ["备份副本 integrity_check: 损坏"],
            )
        elif fail_point == "content_digest":
            monkeypatch.setattr(
                backup_db, "verify_restore",
                lambda s, d: ["表 sample: 内容摘要不一致(非主键数据被篡改)"],
            )
        elif fail_point == "publish":
            def _boom_publish(partial, dst):
                raise OSError("injected publish failure")
            monkeypatch.setattr(backup_db, "_publish", _boom_publish)

        rc = do_backup(str(source), out)
        assert rc != 0, "失败点必须返回非零"
        # 最终 .db 绝不能出现
        assert not list(out.glob("*.db")), "失败不应留下最终 .db"
        # partial / sidecar 必须清理
        assert not list(out.glob("*.partial")), "失败必须清理 partial"

    def test_cleanup_failure_not_swallowed(self, test_artifact_dir, monkeypatch):
        """校验失败且 partial 清理抛错: 必须返回非零并报告, 不得声称成功(R7-06)。"""
        from pathlib import Path as _Path

        source = test_artifact_dir / "src.db"
        _make_source(source, rows=1)
        out = test_artifact_dir / "out"
        out.mkdir()

        # 让 verify 失败
        monkeypatch.setattr(
            backup_db, "verify_restore",
            lambda s, d: ["表 sample: 行数不一致(源 1 vs 备份 0)"],
        )
        # 让清理抛错: Path.unlink 直接失败
        def _boom_unlink(self):
            raise OSError("injected cleanup failure")

        monkeypatch.setattr(_Path, "unlink", _boom_unlink)

        rc = do_backup(str(source), out)
        assert rc != 0, "清理失败必须返回非零"
        # 因清理失败, 该 partial 可能仍在; 重点是不得声称成功(rc==0)
        assert rc != 0

    def test_extra_table_rejected_before_publish(self, test_artifact_dir, monkeypatch):
        """R7-07: partial 被注入额外表, 完整比较必须失败, 不发布最终 .db。"""
        source = test_artifact_dir / "src.db"
        _make_source(source, rows=1)
        out = test_artifact_dir / "out"
        out.mkdir()

        def _inject(partial):
            with closing(sqlite3.connect(partial)) as conn:
                conn.execute("CREATE TABLE attacker_extra (id INTEGER PRIMARY KEY)")
                conn.commit()

        backup_db._POST_WRITE_HOOK = _inject
        try:
            rc = do_backup(str(source), out)
        finally:
            backup_db._POST_WRITE_HOOK = None
        assert rc != 0, "额外表必须被拒绝"
        assert not list(out.glob("*.db")), "额外表副本不得发布"
        assert not list(out.glob("*.partial")), "额外表 partial 必须清理"

    def test_content_tamper_rejected_before_publish(self, test_artifact_dir, monkeypatch):
        """内容篡改(删行): 行数/摘要不一致必须失败, 不发布最终 .db。"""
        source = test_artifact_dir / "src.db"
        _make_source(source, rows=2)
        out = test_artifact_dir / "out"
        out.mkdir()

        def _inject(partial):
            with closing(sqlite3.connect(partial)) as conn:
                conn.execute("DELETE FROM sample WHERE id = 1")
                conn.commit()

        backup_db._POST_WRITE_HOOK = _inject
        try:
            rc = do_backup(str(source), out)
        finally:
            backup_db._POST_WRITE_HOOK = None
        assert rc != 0, "内容篡改必须被拒绝"
        assert not list(out.glob("*.db")), "篡改副本不得发布"
        assert not list(out.glob("*.partial")), "篡改 partial 必须清理"

    def test_list_never_shows_partial_or_failed(self, test_artifact_dir, capsys):
        """R7-05/Step5: --list 永远不展示 partial/failed/sidecar。"""
        d = test_artifact_dir / "mix"
        d.mkdir()
        (d / "web.20260910_000000_000000.partial").write_bytes(b"x")
        (d / "web.20260910_000000_000000.failed").write_bytes(b"x")
        rc = list_backups(d)
        assert rc == 0
        out = capsys.readouterr().out
        assert "暂无备份" in out
        assert ".partial" not in out
        assert ".failed" not in out


class TestAtomicPublish:
    """Task 3 / Step4: 原子不覆盖发布原语与并发竞争。"""

    def test_publish_refuses_overwrite(self, test_artifact_dir):
        """dst 已存在时 _publish 必须抛 FileExistsError(不覆盖)。"""
        source = test_artifact_dir / "src.db"
        _make_source(source, rows=1)
        dst = test_artifact_dir / "final.db"
        dst.write_bytes(b"preexisting")
        partial = test_artifact_dir / "final.partial"
        backup(source, partial)  # 写入合法 partial
        with pytest.raises(FileExistsError):
            _publish(partial, dst)

    def test_concurrent_publish_same_dst_only_one_succeeds(self, test_artifact_dir):
        """并发发布同一 dst: 恰好一个成功, 另一个抛 FileExistsError(R7-05 并发原语)。"""
        import threading

        source = test_artifact_dir / "src.db"
        _make_source(source, rows=1)
        dst = test_artifact_dir / "final.db"
        pa = test_artifact_dir / "a.partial"
        pb = test_artifact_dir / "b.partial"
        backup(source, pa)
        backup(source, pb)

        succeeded: dict = {}
        conflicted: dict = {}

        def worker(tag, partial):
            try:
                _publish(partial, dst)
                succeeded[tag] = True
            except FileExistsError:
                conflicted[tag] = True

        t1 = threading.Thread(target=worker, args=("a", pa))
        t2 = threading.Thread(target=worker, args=("b", pb))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert len(succeeded) == 1, f"恰好一个成功, 实际: {succeeded}"
        assert len(conflicted) == 1, f"恰好一个抛 FileExistsError, 实际: {conflicted}"
        assert dst.exists(), "最终 .db 应被发布"
