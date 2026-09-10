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
import os
import subprocess
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


def _configure_console_output() -> None:
    """让窄编码控制台转义不可表示字符，而不是在写操作前后抛编码异常。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (OSError, ValueError):
                pass


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
    """路径护栏: 返回失败原因字符串或 None(通过)。

    copy 不得是日常 data/web.db(硬禁令, 先于存在性检查, 不要求该文件存在);
    source 与 backup_copy 必须存在且为普通文件; 二者不得指向同一文件。
    """
    if backup_copy == _repo_data_web_db():
        return f"拒绝接管目标等于日常库: {_repo_data_web_db()}"
    if not source.exists():
        return f"source 不存在: {source}"
    if not backup_copy.exists():
        return f"backup_copy 不存在: {backup_copy}"
    if not source.is_file():
        return f"source 不是普通文件: {source}"
    if not backup_copy.is_file():
        return f"backup_copy 不是普通文件: {backup_copy}"
    if source == backup_copy:
        return "source 与 backup_copy 指向同一文件, 拒绝接管"
    return None


def _is_explicit_path(raw: str) -> bool:
    """CLI 原始参数必须是绝对文件路径或 sqlite:/// URL, 拒绝相对路径。

    R7-09: 不得先 resolve 后接受——相对路径在 resolve 后可能落到任意 cwd, 偏离
    "显式绝对路径" 要求。"""
    try:
        value = os.fspath(raw)
    except TypeError:
        return False
    if value.startswith("sqlite:///"):
        value = value[len("sqlite:///"):]
    return Path(value).is_absolute()


def _revision_is_valid(revision: str) -> bool:
    """revision 必须能用 ScriptDirectory 解析为迁移图中的合法 revision。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    try:
        ScriptDirectory.from_config(cfg).get_revision(revision)
        return True
    except Exception:
        return False


def adopt_database_copy(source, backup_copy, revision, dependencies) -> AdoptionResult:
    """固定状态机接管编排。

    dependencies 提供键 verify/compare/stamp/revision/upgrade/smoke, 每个为无参
    可调用: 返回 None 或空 list=成功, 非空 list=差异(失败), 抛异常=失败。任一阶段失败
    立即停止并返回非 0 code 与 failed_stage。依赖契约(六个键存在且 callable、返回值
    仅接受 None/list[str])在执行前校验, 违例转译为结构化失败(R7-09)。
    """
    result = AdoptionResult()
    if not _is_explicit_path(source) or not _is_explicit_path(backup_copy):
        result.stages.append(
            StageResult("path_guard", "failed", "source 与 backup_copy 必须是绝对路径")
        )
        result.failed_stage = "path_guard"
        result.code = 2
        return result
    src = _resolve_arg(os.fspath(source))
    dst = _resolve_arg(os.fspath(backup_copy))

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

    # 依赖契约: 六个键必须存在且 callable, 违例为结构化失败(R7-09)
    expected_keys = [k for _, k in STAGES]
    for key in expected_keys:
        if key not in dependencies:
            result.stages.append(
                StageResult("dependency_contract", "failed", f"缺少 dependency 键: {key}")
            )
            result.failed_stage = "dependency_contract"
            result.code = 2
            return result
        if not callable(dependencies[key]):
            result.stages.append(
                StageResult("dependency_contract", "failed", f"dependency 键 {key} 不可调用")
            )
            result.failed_stage = "dependency_contract"
            result.code = 2
            return result

    for stage_name, dep_key in STAGES:
        # 依赖查找放进异常转译边界(R7-09: 缺键/不可调用已在上面拦截, 此处仅兜底)
        try:
            dep = dependencies[dep_key]
            report = dep()
        except Exception as exc:  # 不捕获后继续: 立即停
            result.stages.append(
                StageResult(stage_name, "failed", f"{type(exc).__name__}: {exc}")
            )
            result.failed_stage = stage_name
            result.code = 1
            return result
        # 返回值只接受 None 或 list[str]; 其他类型视为非法, 形成结构化失败
        if report is None or (isinstance(report, list) and not report):
            result.stages.append(StageResult(stage_name, "passed"))
        elif isinstance(report, list) and all(isinstance(item, str) for item in report):
            result.stages.append(StageResult(stage_name, "failed", "; ".join(report)))
            result.failed_stage = stage_name
            result.code = 1
            return result
        elif isinstance(report, list):
            result.stages.append(
                StageResult(stage_name, "failed", "阶段返回类型非法: 仅接受 list[str]")
            )
            result.failed_stage = stage_name
            result.code = 1
            return result
        else:
            result.stages.append(
                StageResult(
                    stage_name,
                    "failed",
                    f"阶段返回类型非法: {type(report).__name__}, 仅接受 None 或 list[str]",
                )
            )
            result.failed_stage = stage_name
            result.code = 1
            return result

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
        try:
            with eng.connect() as conn:
                return MigrationContext.configure(conn).get_current_revision()
        finally:
            eng.dispose()

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
        """在绑定副本的全新子进程中隔离冒烟, 不碰父进程 DATABASE_URL 指向的库(R7-01)。

        子进程 env 强制 DATABASE_URL=副本、SCHEDULER_ENABLED=false、PYTHONUTF8=1,
        不污染父进程环境; 子进程用 -X utf8 进一步保证 UTF-8。失败(非 0 或缺少 [PASS])
        抛 RuntimeError 并附子进程 stderr 尾部, 由状态机转译为结构化失败结果。
        """
        child_env = dict(os.environ)
        child_env["DATABASE_URL"] = f"sqlite:///{backup_copy.as_posix()}"
        child_env["SCHEDULER_ENABLED"] = "false"
        child_env["PYTHONUTF8"] = "1"
        # 以字节捕获: 子进程经 -X utf8 输出 UTF-8, 外层可能是 GBK(PYTHONIOENCODING),
        # 按字节读取可避免父子进程编码不一致导致的解码崩溃(R7-03 同类问题)。
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "scripts.smoke_db_copy",
             "--database", str(backup_copy)],
            cwd=str(Path(__file__).resolve().parent.parent),
            check=False,
            capture_output=True,
            timeout=60,
            env=child_env,
        )
        if proc.returncode != 0 or b"[PASS]" not in (proc.stdout or b""):
            tail = b"\n".join((proc.stderr or b"").strip().splitlines()[-10:])
            raise RuntimeError(
                f"隔离冒烟子进程失败(rc={proc.returncode}):\n"
                f"{tail.decode('utf-8', errors='replace')}"
            )
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
    _configure_console_output()
    parser = argparse.ArgumentParser(description="安全接管 SQLite 副本(固定状态机)")
    parser.add_argument("--source", required=True, help="源库(绝对路径或 sqlite:/// URL)")
    parser.add_argument("--backup-copy", required=True, help="待接管副本(绝对路径)")
    parser.add_argument(
        "--revision",
        default="0001",
        help="副本 stamp 后的目标 revision(默认 0001)",
    )
    args = parser.parse_args()

    # 输入护栏: CLI 原始参数必须是绝对路径或 sqlite:/// URL, 拒绝相对路径(R7-09)
    for label, raw in (("--source", args.source), ("--backup-copy", args.backup_copy)):
        if not _is_explicit_path(raw):
            print(
                f"[FAIL] {label} 必须是绝对路径或 sqlite:/// URL, 拒绝相对路径: {raw}",
                file=sys.stderr,
            )
            return 2
    # revision 必须能用 ScriptDirectory 解析为迁移图中的合法 revision(R7-09)
    if not _revision_is_valid(args.revision):
        print(f"[FAIL] 无效 revision(不在迁移图中): {args.revision}", file=sys.stderr)
        return 2

    source = _resolve_arg(args.source)
    backup_copy = _resolve_arg(args.backup_copy)
    deps = build_dependencies(source, backup_copy, args.revision)
    result = adopt_database_copy(source, backup_copy, args.revision, deps)

    for stage in result.stages:
        mark = "[PASS]" if stage.status == "passed" else "[FAIL]"
        suffix = f" — {stage.detail}" if stage.detail else ""
        print(f"{mark} {stage.name}{suffix}")

    if result.code == 0:
        print(f"[PASS] 副本接管完成, revision={args.revision}")
    else:
        print(f"[FAIL] 接管在阶段 {result.failed_stage} 失败", file=sys.stderr)
    return result.code


if __name__ == "__main__":
    sys.exit(main())
