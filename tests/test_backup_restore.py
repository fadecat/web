# -*- coding: utf-8 -*-
"""SQLite 备份与恢复副本验证测试(第四阶段/T7, 第五阶段/T3-T4 收口).

全部在测试临时目录执行, 不读取/复制日常库。
覆盖: WAL 提交行被备份捕获 / 拒绝覆盖已有目标 / 篡改副本被拒 /
结构漂移被拒 / 主键值替换被拒 / 非键内容篡改被拒 / 一致副本通过。
所有 sqlite3 连接用 contextlib.closing 显式关闭(R5-05: 不留句柄)。
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from scripts.backup_db import backup
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
