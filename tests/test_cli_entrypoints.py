# -*- coding: utf-8 -*-
"""真实 subprocess CLI 测试(Task 1 / R7-02, R7-03).

用参数数组 [sys.executable, "-m", "scripts.<module>"] 从仓库根目录运行各运维模块,
证明 runbook 文档的 module CLI 命令可被真实进程执行(R7-02), 且 Windows GBK 控制台
下成功/失败链均输出 ASCII 状态标记([PASS]/[FAIL])且不抛 UnicodeEncodeError(R7-03).

所有命令均通过真实子进程执行, 不得以 Python 函数调用替代 CLI。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine

from backend.models.app_setting import AppSetting
from backend.models.database import Base
from backend.models import app_setting, data_status, valuation  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _run_module(module: str, args, env=None, encoding: str = "utf-8") -> subprocess.CompletedProcess:
    """以真实 subprocess 运行 `python -m scripts.<module>`, 返回 CompletedProcess。

    encoding 仅控制子进程 PYTHONIOENCODING; 输出以 bytes 捕获并手动解码,
    避免父进程用不同编码读取 GBK 字节时崩溃。
    """
    base = dict(os.environ)
    base["PYTHONIOENCODING"] = encoding
    if env:
        base.update(env)
    return subprocess.run(
        [PY, "-m", f"scripts.{module}", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=180,
        env=base,
        check=False,
    )


def _decode(cp: subprocess.CompletedProcess) -> str:
    """将子进程字节输出合并为字符串(GBK 字节以 replace 解码, 防止父进程解码崩溃)。"""
    out = b""
    if cp.stdout:
        out += cp.stdout
    if cp.stderr:
        out += cp.stderr
    return out.decode("gbk", errors="replace")


def _build_source(db_path: Path) -> None:
    """构造未 stamp 的 ORM 结构库, 含一行业务数据(复刻 test_database_adoption)。"""
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            AppSetting.__table__.insert(),
            {"key": "smtp_host", "value": "example.invalid"},
        )
    engine.dispose()


# ----- Step 1: 真实 subprocess --help, 证明模块可被执行(R7-02) -----


def test_checker_help():
    cp = _run_module("check_db_baseline", ["--help"])
    assert cp.returncode == 0, _decode(cp)
    assert "database-url" in _decode(cp)


def test_verify_help():
    cp = _run_module("verify_db_restore", ["--help"])
    assert cp.returncode == 0, _decode(cp)
    assert "backup" in _decode(cp)


def test_adopt_help():
    cp = _run_module("adopt_db_copy", ["--help"])
    assert cp.returncode == 0, _decode(cp)
    assert "backup-copy" in _decode(cp)


def test_smoke_help():
    cp = _run_module("smoke_db_copy", ["--help"])
    assert cp.returncode == 0, _decode(cp)
    assert "database" in _decode(cp)


# ----- Step 1: 临时库 happy path, 真实 module CLI 完成一次数据操作 -----


def test_checker_runs_on_built_db(tmp_path):
    """checker 真实 module CLI 对 create_all 出来的库做结构核对通过。"""
    source = tmp_path / "source.db"
    _build_source(source)
    cp = _run_module(
        "check_db_baseline",
        ["--database-url", f"sqlite:///{source.as_posix()}"],
    )
    assert cp.returncode == 0, _decode(cp)


# ----- Step 3: GBK 控制台下成功/失败链, 断言 ASCII 状态标记与退出码(R7-03) -----


def test_adopt_chain_gbk_success(tmp_path):
    """GBK 控制台下完整接管链成功: 输出含 [PASS], 退出码 0, 无 UnicodeEncodeError。"""
    source = tmp_path / "source.db"
    backups = tmp_path / "backups"
    backups.mkdir()
    _build_source(source)

    # 备份生成副本(module CLI)
    bk = _run_module(
        "backup_db",
        ["--source", str(source), "--destination-dir", str(backups)],
    )
    assert bk.returncode == 0, _decode(bk)
    copies = [p for p in backups.iterdir() if p.suffix == ".db"]
    assert copies, _decode(bk)
    copy = copies[0]

    env = {
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'unrelated.db').as_posix()}",
        "SCHEDULER_ENABLED": "false",
    }
    cp = _run_module(
        "adopt_db_copy",
        ["--source", str(source), "--backup-copy", str(copy), "--revision", "0001"],
        env=env,
        encoding="gbk",
    )
    text = _decode(cp)
    assert cp.returncode == 0, text
    assert "[PASS]" in text, text
    assert "UnicodeEncodeError" not in text, text


def test_adopt_chain_gbk_failure(tmp_path):
    """GBK 控制台下接管失败链: 输出含 [FAIL], 退出码非 0, 无 UnicodeEncodeError。"""
    source = tmp_path / "source.db"
    _build_source(source)
    missing = tmp_path / "does_not_exist.db"  # 副本不存在 → verify 阶段异常

    env = {
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'unrelated.db').as_posix()}",
        "SCHEDULER_ENABLED": "false",
    }
    cp = _run_module(
        "adopt_db_copy",
        ["--source", str(source), "--backup-copy", str(missing), "--revision", "0001"],
        env=env,
        encoding="gbk",
    )
    text = _decode(cp)
    assert cp.returncode != 0, text
    assert "[FAIL]" in text, text
    assert "UnicodeEncodeError" not in text, text
