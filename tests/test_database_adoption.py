# -*- coding: utf-8 -*-
"""完整副本接管链测试(Task 5, R6-03/R6-04; Task 2 R7-01 隔离反例).

happy path: 调用真实 adopt_database_copy(verify/compare/stamp/revision/
upgrade/smoke 全真实, smoke 为绑定副本的子进程), 最终状态必须为 smoke_passed,
数据与版本号正确落库; 父进程 DATABASE_URL 指向的 unrelated 哨兵完全不变。
漂移库: 真实编排入口在 schema_verified 阶段失败, stamp/upgrade/smoke 0 次调用。
另覆盖状态机全部失败分支: 任一阶段失败 → 后续依赖 0 次调用、返回非零且含阶段名。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine, text

from backend.models.app_setting import AppSetting
from backend.models.database import Base
from backend.models import app_setting, data_status, valuation  # noqa: F401

from scripts.backup_db import backup

REPO_ROOT = Path(__file__).resolve().parent.parent


def _build_unversioned_source(db_path: Path) -> None:
    """构造未 stamp 的 ORM 结构库, 含一行业务数据。"""
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            AppSetting.__table__.insert(),
            {"key": "smtp_host", "value": "example.invalid"},
        )
    engine.dispose()


def _snapshot(db_path: Path) -> "tuple[str, float, tuple]":
    """快照(字节哈希, mtime, 排序表集合), 用于证明库未被接管冒烟改写。"""
    with closing(sqlite3.connect(db_path)) as conn:
        tables = tuple(sorted(
            r[0] for r in conn.execute(
                "select name from sqlite_master where type='table'"
            )
        ))
    return (
        hashlib.sha256(db_path.read_bytes()).hexdigest(),
        round(db_path.stat().st_mtime, 3),
        tables,
    )


class TestDatabaseAdoption:
    def test_unversioned_database_copy_can_be_adopted(self, test_artifact_dir):
        """完整接管链: 真实 build_dependencies()(含子进程 smoke)跑通;
        unrelated 哨兵(父进程 DATABASE_URL 指向的库)完全不变, copy 达到 0001, 副本
        数据/版本号正确落库。"""
        source = test_artifact_dir / "source.db"
        backup_copy = test_artifact_dir / "backup.db"
        _build_unversioned_source(source)
        backup(source, backup_copy)

        # 哨兵: 与业务无关的库, 接管前快照字节/mtime/表集合
        unrelated = test_artifact_dir / "unrelated.db"
        with closing(sqlite3.connect(unrelated)) as conn:
            conn.execute("create table sentinel_marker (id integer primary key)")
            conn.commit()
        before = _snapshot(unrelated)

        # 父进程 DATABASE_URL 指向 unrelated: 若 smoke 仍进全局 lifespan, unrelated 会被改写
        os.environ["DATABASE_URL"] = f"sqlite:///{unrelated.as_posix()}"
        os.environ["SCHEDULER_ENABLED"] = "false"

        from scripts.adopt_db_copy import adopt_database_copy, build_dependencies

        # 真实 build_dependencies: smoke 为绑定副本的子进程, 不再手工覆盖
        deps = build_dependencies(source, backup_copy, "0001")

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code == 0, [f"{s.name}:{s.status}:{s.detail}" for s in result.stages]
        assert result.failed_stage is None
        assert result.stages[-1].name == "smoke_passed"

        # unrelated 必须完全未变(证明子进程 smoke 不继承父进程 DATABASE_URL)
        after = _snapshot(unrelated)
        assert after == before, f"unrelated 被接管冒烟改写: {before} -> {after}"

        with closing(sqlite3.connect(backup_copy)) as conn:
            assert conn.execute(
                "select value from app_setting where key='smtp_host'"
            ).fetchone() == ("example.invalid",)
            assert conn.execute("select version_num from alembic_version").fetchone() == ("0001",)

    def test_adopt_module_cli_leaves_unrelated_untouched(self, test_artifact_dir):
        """R7-01: 真实模块 adopt CLI 的 smoke 必须放进绑定副本的全新子进程,
        不碰父进程 DATABASE_URL 指向的 unrelated 库。

        先失败反例: 修复前 adopt 在父进程调用 run_smoke(), 进入全局 backend lifespan,
        把 init_db 打到全局 DATABASE_URL(=unrelated)上, unrelated 会被改写; 修复后
        子进程 smoke 只绑定副本, unrelated 字节/mtime/表集合完全不变。
        """
        source = test_artifact_dir / "source.db"
        backup_copy = test_artifact_dir / "backup.db"
        _build_unversioned_source(source)
        backup(source, backup_copy)

        # 哨兵: 与业务无关的库, 接管前快照
        unrelated = test_artifact_dir / "unrelated.db"
        with closing(sqlite3.connect(unrelated)) as conn:
            conn.execute("create table sentinel_marker (id integer primary key)")
            conn.commit()
        before = _snapshot(unrelated)

        # 父进程 DATABASE_URL 指向 unrelated
        env = dict(os.environ)
        env["DATABASE_URL"] = f"sqlite:///{unrelated.as_posix()}"
        env["SCHEDULER_ENABLED"] = "false"
        cp = subprocess.run(
            [sys.executable, "-m", "scripts.adopt_db_copy",
             "--source", str(source), "--backup-copy", str(backup_copy), "--revision", "0001"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=env,
        )
        assert cp.returncode == 0, cp.stdout + cp.stderr
        assert "smoke_passed" in cp.stdout, cp.stdout

        # unrelated 必须完全未变
        after = _snapshot(unrelated)
        assert after == before, f"unrelated 被接管冒烟改写: {before} -> {after}"

        # copy 应达到 0001
        with closing(sqlite3.connect(backup_copy)) as conn:
            assert conn.execute("select version_num from alembic_version").fetchone() == ("0001",)

    def test_drifted_database_never_calls_stamp(self, test_artifact_dir):
        """缺列漂移库: 真实 adopt_database_copy 在 schema_verified 阶段失败,
        且 stamp / upgrade / smoke 均未被调用(fail closed)。"""
        source = test_artifact_dir / "drift.db"
        engine = create_engine(f"sqlite:///{source.as_posix()}")
        try:
            Base.metadata.create_all(engine)
            with engine.begin() as conn:
                # 删除一列制造漂移
                conn.execute(text("ALTER TABLE app_setting DROP COLUMN updated_at"))
        finally:
            engine.dispose()
        backup_copy = test_artifact_dir / "drift_backup.db"
        backup(source, backup_copy)

        from scripts.adopt_db_copy import adopt_database_copy, build_dependencies

        deps = build_dependencies(source, backup_copy, "0001")
        stamp = _CallCounter()
        upgrade = _CallCounter()
        smoke = _CallCounter()
        deps["stamp"] = stamp
        deps["upgrade"] = upgrade
        deps["smoke"] = smoke

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code != 0
        assert result.failed_stage == "schema_verified"
        assert stamp.calls == 0
        assert upgrade.calls == 0
        assert smoke.calls == 0

    def test_extra_table_database_never_calls_stamp(self, test_artifact_dir):
        """多余表漂移库: 真实 adopt_database_copy 在 schema_verified 阶段失败,
        且 stamp / upgrade / smoke 均未被调用(fail closed)。"""
        import sqlite3 as sq

        source = test_artifact_dir / "extra.db"
        with closing(sq.connect(source)) as conn:
            with conn:
                conn.execute("create table unexpected_table (id integer primary key)")
        backup_copy = test_artifact_dir / "extra_backup.db"
        backup(source, backup_copy)

        from scripts.adopt_db_copy import adopt_database_copy, build_dependencies

        deps = build_dependencies(source, backup_copy, "0001")
        stamp = _CallCounter()
        upgrade = _CallCounter()
        smoke = _CallCounter()
        deps["stamp"] = stamp
        deps["upgrade"] = upgrade
        deps["smoke"] = smoke

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code != 0
        assert result.failed_stage == "schema_verified"
        assert stamp.calls == 0
        assert upgrade.calls == 0
        assert smoke.calls == 0


class _CallCounter:
    """可注入的接管依赖: 记录调用次数, 返回预设差异列表或抛异常。"""

    def __init__(self, returns=None, raises=None):
        self.calls = 0
        self.returns = returns  # list -> 作为差异返回; None -> 返回 []
        self.raises = raises  # 抛出的异常实例

    def __call__(self):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.returns if self.returns is not None else []


class TestAdoptionOrchestration:
    """Task 5 / R6-03: 接管编排状态机失败优先测试。

    直接调用 adopt_database_copy(source, backup_copy, revision, dependencies),
    注入可计数的 verify/compare/stamp/revision/upgrade/smoke 依赖, 断言:
    - verify 失败 → 后续 5 个依赖 0 次调用
    - compare 失败 → stamp/revision/upgrade/smoke 0 次
    - stamp 失败 → revision/upgrade/smoke 0 次
    - upgrade 失败 → smoke 0 次
    - 任一步失败 → 返回非零且结果含失败阶段名
    """

    def _source_and_copy(self, tmp):
        source = tmp / "source.db"
        backup_copy = tmp / "backup.db"
        _build_unversioned_source(source)
        backup(source, backup_copy)
        return source, backup_copy

    def _make_deps(self, **overrides):
        deps = {
            "verify": _CallCounter(),
            "compare": _CallCounter(),
            "stamp": _CallCounter(),
            "revision": _CallCounter(),
            "upgrade": _CallCounter(),
            "smoke": _CallCounter(),
        }
        deps.update(overrides)
        return deps

    def test_verify_failure_blocks_all_downstream(self, test_artifact_dir):
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        verify = _CallCounter(returns=["integrity_check: corrupt"])
        deps = self._make_deps(verify=verify)
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code != 0
        assert result.failed_stage == "backup_verified"
        assert verify.calls == 1
        for key in ("compare", "stamp", "revision", "upgrade", "smoke"):
            assert deps[key].calls == 0, key

    def test_compare_failure_blocks_stamp_and_after(self, test_artifact_dir):
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        compare = _CallCounter(returns=["app_setting 缺列"])
        deps = self._make_deps(compare=compare)
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code != 0
        assert result.failed_stage == "schema_verified"
        assert compare.calls == 1
        for key in ("stamp", "revision", "upgrade", "smoke"):
            assert deps[key].calls == 0, key

    def test_stamp_failure_blocks_revision_and_after(self, test_artifact_dir):
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        stamp = _CallCounter(raises=RuntimeError("stamp failed"))
        deps = self._make_deps(stamp=stamp)
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code != 0
        assert result.failed_stage == "stamped"
        assert stamp.calls == 1
        for key in ("revision", "upgrade", "smoke"):
            assert deps[key].calls == 0, key

    def test_upgrade_failure_blocks_smoke(self, test_artifact_dir):
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        upgrade = _CallCounter(raises=RuntimeError("upgrade failed"))
        deps = self._make_deps(upgrade=upgrade)
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code != 0
        assert result.failed_stage == "upgraded"
        assert upgrade.calls == 1
        assert deps["smoke"].calls == 0

    def test_all_stages_pass_returns_zero(self, test_artifact_dir):
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        deps = self._make_deps()
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code == 0
        assert result.failed_stage is None
        assert [s.name for s in result.stages] == [
            "backup_verified",
            "schema_verified",
            "stamped",
            "revision_verified",
            "upgraded",
            "smoke_passed",
        ]
        for stage in result.stages:
            assert stage.status == "passed", stage.name

    def test_revision_failure_blocks_upgrade_and_smoke(self, test_artifact_dir):
        """revision 阶段失败: upgrade / smoke 0 次调用, failed_stage 精确。"""
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        revision = _CallCounter(returns=["migration revision 不一致(源 None vs 副本 0001)"])
        deps = self._make_deps(revision=revision)
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code != 0
        assert result.failed_stage == "revision_verified"
        assert revision.calls == 1
        assert deps["upgrade"].calls == 0
        assert deps["smoke"].calls == 0

    def test_smoke_failure_blocks_nothing_after(self, test_artifact_dir):
        """smoke 阶段失败: 已是最后阶段, failed_stage 精确, code 非 0。"""
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        smoke = _CallCounter(raises=RuntimeError("隔离冒烟子进程失败"))
        deps = self._make_deps(smoke=smoke)
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code != 0
        assert result.failed_stage == "smoke_passed"
        # 前 5 阶段必须全部通过
        assert [s.name for s in result.stages[:-1]] == [
            "backup_verified",
            "schema_verified",
            "stamped",
            "revision_verified",
            "upgraded",
        ]
        assert smoke.calls == 1

    def test_missing_dependency_fails_contract(self, test_artifact_dir):
        """缺 dependency 键: 结构化失败 dependency_contract, code 2, 不进入任何阶段。"""
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        deps = self._make_deps()
        del deps["upgrade"]  # 模拟缺键
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code == 2
        assert result.failed_stage == "dependency_contract"
        assert len(result.stages) == 1
        assert "缺少 dependency 键" in result.stages[0].detail
        assert "upgrade" not in deps
        for key in ("verify", "compare", "stamp", "revision", "smoke"):
            assert deps[key].calls == 0, key

    def test_noncallable_dependency_fails_contract(self, test_artifact_dir):
        """dependency 键不可调用: 结构化失败 dependency_contract, code 2。"""
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        deps = self._make_deps()
        deps["stamp"] = "not-callable"  # 不可调用
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code == 2
        assert result.failed_stage == "dependency_contract"
        assert "不可调用" in result.stages[0].detail

    def test_wrong_return_type_fails_stage(self, test_artifact_dir):
        """阶段返回非 None/非 list 类型: 视为非法, 结构化失败于该阶段, code 1。

        R7-09: 旧实现会把此类返回值当成功(返回空 list 之外), 这里必须失败。
        """
        source, backup_copy = self._source_and_copy(test_artifact_dir)
        bad = _CallCounter(returns="not-a-list")  # callable 但返回 str
        deps = self._make_deps(compare=bad)
        from scripts.adopt_db_copy import adopt_database_copy

        result = adopt_database_copy(source, backup_copy, "0001", deps)
        assert result.code != 0
        assert result.failed_stage == "schema_verified"
        assert bad.calls == 1
        for key in ("stamp", "revision", "upgrade", "smoke"):
            assert deps[key].calls == 0, key


class TestAdoptionCliInput:
    """Task 4 / R7-09: CLI 输入护栏(相对路径 / 无效 revision / 不存在文件 / 日常库)。

    均通过真实 subprocess 运行 python -m scripts.adopt_db_copy, 不得碰 data/。
    """

    def _run(self, args, env=None):
        base = dict(os.environ)
        base["SCHEDULER_ENABLED"] = "false"
        if env:
            base.update(env)
        return subprocess.run(
            [sys.executable, "-m", "scripts.adopt_db_copy", *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=base,
        )

    def test_relative_source_rejected(self, test_artifact_dir):
        """相对路径必须被 CLI 拒绝(返回 2), 不得先 resolve 后接受。"""
        cp = self._run([
            "--source", "relative_source.db",
            "--backup-copy", str(test_artifact_dir / "copy.db"),
            "--revision", "0001",
        ])
        assert cp.returncode == 2, cp.stdout + cp.stderr
        assert "相对路径" in (cp.stdout + cp.stderr)

    def test_relative_backup_copy_rejected(self, test_artifact_dir):
        source = test_artifact_dir / "src.db"
        _build_unversioned_source(source)
        cp = self._run([
            "--source", str(source),
            "--backup-copy", "relative_copy.db",
            "--revision", "0001",
        ])
        assert cp.returncode == 2, cp.stdout + cp.stderr
        assert "相对路径" in (cp.stdout + cp.stderr)

    def test_invalid_revision_rejected(self, test_artifact_dir):
        """无效 revision(不在迁移图中): CLI 返回 2。"""
        source = test_artifact_dir / "src.db"
        backup_copy = test_artifact_dir / "copy.db"
        _build_unversioned_source(source)
        backup(source, backup_copy)
        cp = self._run([
            "--source", str(source),
            "--backup-copy", str(backup_copy),
            "--revision", "9999",
        ])
        assert cp.returncode == 2, cp.stdout + cp.stderr
        assert "无效 revision" in (cp.stdout + cp.stderr)

    def test_missing_source_rejected(self, test_artifact_dir):
        """source 不存在: path_guard 失败, 返回 2。"""
        source = test_artifact_dir / "absent_source.db"  # 故意不存在
        backup_copy = test_artifact_dir / "copy.db"
        _build_unversioned_source(backup_copy)  # copy 存在即可
        cp = self._run([
            "--source", str(source),
            "--backup-copy", str(backup_copy),
            "--revision", "0001",
        ])
        assert cp.returncode == 2, cp.stdout + cp.stderr
        assert "source 不存在" in (cp.stdout + cp.stderr)

    def test_copy_equal_daily_db_rejected(self, test_artifact_dir):
        """copy 指向日常 data/web.db: 硬禁令, 返回 2(不要求该文件存在, 不碰 data/)。"""
        from scripts.adopt_db_copy import adopt_database_copy, _repo_data_web_db

        source = test_artifact_dir / "src.db"
        _build_unversioned_source(source)
        daily = _repo_data_web_db()
        # path_guard 先于依赖契约检查, 以下依赖不会被调用
        deps = {
            "verify": _CallCounter(),
            "compare": _CallCounter(),
            "stamp": _CallCounter(),
            "revision": _CallCounter(),
            "upgrade": _CallCounter(),
            "smoke": _CallCounter(),
        }
        result = adopt_database_copy(source, daily, "0001", deps)
        assert result.code == 2
        assert result.failed_stage == "path_guard"
        assert "日常库" in result.stages[0].detail
