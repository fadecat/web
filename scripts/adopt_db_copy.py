# -*- coding: utf-8 -*-
"""副本接管编排入口(Task 5 / R6-03, R6-04 的编排侧).

单入口状态机:
    backup_verified → schema_verified → stamped → revision_verified
    → upgraded → smoke_passed

不跳步、不捕获后继续; 任一阶段失败立即停止, 返回非 0 与失败阶段名。所有路径
必须显式传入且为绝对路径; 拒绝接管目标等于日常 data/web.db, 拒绝 source 与
backup_copy 指向同一文件。只操作调用方提供的副本, 不碰日常库。

依赖注入: adopt_database_copy 的 dependencies 必须提供键 verify / compare /
stamp / revision / upgrade / smoke, 每个值为无参可调用, 返回空 list 表示成功、
非空 list 表示差异(失败)、或抛异常。生产路径用 build_dependencies() 把真实
verify_restore / compare_schema / alembic stamp / upgrade / smoke 接上。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path


# 状态机顺序: (阶段名, dependencies 键)
STAGES = (
    ("backup_verified", "verify"),
    ("schema_verified", "compare"),
    ("stamped", "stamp"),
    ("revision_verified", "revision"),
    ("upgraded", "upgrade"),
    ("smoke_passed", "smoke"),
)


@dataclass
class StageResult:
    """单个阶段的结果。"""

    name: str
    status: str  # "passed" | "failed"
    detail: str = ""


@dataclass
class AdoptionResult:
    """整条接管链的结构化结果。"""

    stages: list[StageResult] = field(default_factory=list)
    failed_stage: "str | None" = None
    code: int = 0


def _repo_data_web_db() -> Path:
    """仓库日常库绝对路径(接管护栏的禁止目标)。"""
    return Path(__file__).resolve().parent.parent / "data" / "web.db"


def _guard_paths(source: Path, backup_copy: Path) -> "str | None":
    """路径护栏: 返回失败原因字符串或 None(通过)。"""
    if source == backup_copy:
        return "source 与 backup_copy 指向同一文件, 拒绝接管"
    if backup_copy == _repo_data_web_db():
        return f"拒绝接管目标等于日常库: {_repo_data_web_db()}"
    return None


def adopt_database_copy(source, backup_copy, revision, dependencies) -> AdoptionResult:
    """固定状态机接管编排。

    dependencies 提供键 verify/compare/stamp/revision/upgrade/smoke, 每个为无参
    可调用: 返回空 list=成功, 非空 list=差异(失败), 抛异常=失败。任一阶段失败
    立即停止并返回非 0 code 与 failed_stage。
    """
    result = AdoptionResult()
    src = Path(source).resolve()
    dst = Path(backup_copy).resolve()

    # 路径护栏(不计入业务阶段, 但失败同样返回非 0)
    guard_reason = _guard_paths(src, dst)
    if guard_reason is not None:
        result.stages.append(StageResult("path_guard", "failed", guard_reason))
        result.failed_stage = "path_guard"
        result.code = 2
        return result

    if not revision or not str(revision).strip():
        result.stages.append(StageResult("revision_arg", "failed", "revision 不能为空"))
        result.failed_stage = "revision_arg"
        result.code = 2
        return result

    for stage_name, dep_key in STAGES:
        dep = dependencies[dep_key]
        try:
            report = dep()
        except Exception as exc:  # 不捕获后继续: 立即停
            result.stages.append(
                StageResult(stage_name, "failed", f"{type(exc).__name__}: {exc}")
            )
            result.failed_stage = stage_name
            result.code = 1
            return result
        if isinstance(report, list) and report:
            result.stages.append(StageResult(stage_name, "failed", "; ".join(report)))
            result.failed_stage = stage_name
            result.code = 1
            return result
        result.stages.append(StageResult(stage_name, "passed"))

    return result


def _resolve_arg(raw: str) -> Path:
    """接受 sqlite:///... URL 或纯文件路径, 返回绝对路径。"""
    if raw.startswith("sqlite:///"):
        return Path(raw[len("sqlite:///"):]).resolve()
    return Path(raw).resolve()


def build_dependencies(source: Path, backup_copy: Path, revision: str) -> dict:
    """把真实的 verify/compare/stamp/upgrade/smoke 接到状态机。

    - verify: 内容级恢复验证(不含 ORM 结构, 留给 compare 阶段)
    - compare: 结构核对(compare_schema)
    - stamp: alembic stamp revision
    - revision: stamp 后再次用 Task 3 显式 revision 策略验证
    - upgrade: alembic upgrade head, 并校验 current 等于 head
    - smoke: 隔离应用冒烟(run_smoke)
    """
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    from backend.models.database import Base
    from backend.models import app_setting, data_status, valuation  # noqa: F401

    from scripts.check_db_baseline import compare_schema
    from scripts.smoke_db_copy import run_smoke
    from scripts.verify_db_restore import verify_restore

    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"

    def _cfg() -> Config:
        cfg = Config()
        cfg.set_main_option("script_location", str(migrations_dir))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{backup_copy.as_posix()}")
        return cfg

    def _verify():
        return verify_restore(source, backup_copy)

    def _compare():
        return compare_schema(f"sqlite:///{backup_copy.as_posix()}", Base.metadata)

    def _stamp():
        command.stamp(_cfg(), revision)
        return []

    def _revision():
        # Task 3 显式 revision 策略: 源未版本化 + 副本等于指定 revision
        return verify_restore(
            source,
            backup_copy,
            Base.metadata,
            expected_backup_revision=revision,
        )

    def _current_revision() -> "str | None":
        """程序化读取副本当前 revision(不依赖 command.current 的打印副作用)。"""
        eng = create_engine(f"sqlite:///{backup_copy.as_posix()}")
        with eng.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()

    def _upgrade():
        command.upgrade(_cfg(), "head")
        heads = ScriptDirectory.from_config(_cfg()).get_heads()
        current = _current_revision()
        if current not in heads:
            raise RuntimeError(
                f"upgrade 后 revision {current!r} 不等于 head {heads!r}"
            )
        return []

    def _smoke():
        run_smoke(backup_copy)
        return []

    return {
        "verify": _verify,
        "compare": _compare,
        "stamp": _stamp,
        "revision": _revision,
        "upgrade": _upgrade,
        "smoke": _smoke,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="安全接管 SQLite 副本(固定状态机)")
    parser.add_argument("--source", required=True, help="源库(绝对路径或 sqlite:/// URL)")
    parser.add_argument("--backup-copy", required=True, help="待接管副本(绝对路径)")
    parser.add_argument(
        "--revision",
        default="0001",
        help="副本 stamp 后的目标 revision(默认 0001)",
    )
    args = parser.parse_args()

    source = _resolve_arg(args.source)
    backup_copy = _resolve_arg(args.backup_copy)
    deps = build_dependencies(source, backup_copy, args.revision)
    result = adopt_database_copy(source, backup_copy, args.revision, deps)

    for stage in result.stages:
        mark = "✅" if stage.status == "passed" else "❌"
        suffix = f" — {stage.detail}" if stage.detail else ""
        print(f"{mark} {stage.name}{suffix}")

    if result.code == 0:
        print(f"✅ 副本接管完成, revision={args.revision}")
    else:
        print(f"❌ 接管在阶段 {result.failed_stage} 失败", file=sys.stderr)
    return result.code


if __name__ == "__main__":
    sys.exit(main())
