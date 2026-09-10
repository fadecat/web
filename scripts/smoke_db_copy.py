# -*- coding: utf-8 -*-
"""隔离应用冒烟脚本(Task 5 / R6-04)。

用临时副本启动应用, 依次请求 /api/health 与真实读库路由 /api/settings, 证明
请求确实使用指定副本(而非内存库或日常 data/web.db)。

配置加载时机: backend.config.settings 与 backend.models.database.engine 在 import
时固化。本脚本在 import 任何 backend.* 之前设置 DATABASE_URL / SCHEDULER_ENABLED,
确保全局 engine 也指向副本; 同时 run_smoke 用依赖覆盖(get_db)把会话绑定到副本,
双重保证即使在已导入 backend 的测试进程里也能读取正确副本。

用法:
    python scripts/smoke_db_copy.py --database D:/path/to/backups/web.<ts>.db
    python scripts/smoke_db_copy.py --database <copy> \
        --expect-key smtp_host --expect-value example.invalid
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def run_smoke(
    database_path: "str | Path",
    expect_key: "str | None" = None,
    expect_value: "str | None" = None,
) -> None:
    """隔离启动应用并访问真实读库路由, 证明请求使用指定副本。

    在导入任何 backend.* 之前先绑定副本(配置 import 时固化), 并断言全局 engine
    确实解析到副本(隔离边界); 不覆盖 get_db, 路由与真实 lifespan 都通过全局
    SessionLocal 命中副本。真实 lifespan 的 init_db 此时也会执行, 但只打在副本上
    (符合"副本允许 init_db"的写入边界, R7-01)。
    """
    copy_path = Path(database_path).resolve()
    if not copy_path.is_file():
        raise RuntimeError(f"副本不存在: {copy_path}")

    # 配置 import 时固化: 必须先设环境变量再导入 backend.*
    os.environ["DATABASE_URL"] = f"sqlite:///{copy_path.as_posix()}"
    os.environ["SCHEDULER_ENABLED"] = "false"

    from backend.main import create_app
    from backend.models.database import SessionLocal, engine as global_engine, get_db
    from fastapi.testclient import TestClient

    # 隔离边界断言: 全局 engine 必须解析到副本, 否则说明冒烟会打到其它库
    resolved_global = Path(str(global_engine.url.database)).resolve()
    if resolved_global != copy_path:
        raise RuntimeError(
            f"冒烟全局 engine 未绑定副本: {resolved_global} != {copy_path}"
        )

    # 不再使用 dependency_overrides: get_db 的真实 SessionLocal 已绑定副本 engine,
    # 真实 lifespan 的 init_db 同样只作用于副本。
    app = create_app()
    try:
        with TestClient(app) as client:
            health = client.get("/api/health")
            if health.status_code != 200 or health.json().get("status") != "ok":
                raise RuntimeError(
                    f"/api/health 未通过: {health.status_code} {health.text}"
                )
            settings_resp = client.get("/api/settings")
            if settings_resp.status_code != 200:
                raise RuntimeError(
                    f"/api/settings 未通过: {settings_resp.status_code} {settings_resp.text}"
                )
            items = settings_resp.json().get("items", {})
            if expect_key is not None:
                entry = items.get(expect_key)
                if entry is None:
                    raise RuntimeError(f"/api/settings 未返回 {expect_key}")
                actual = entry.get("value")
                if actual != expect_value:
                    raise RuntimeError(
                        f"副本 app_setting[{expect_key}]={actual!r} "
                        f"不等于预期 {expect_value!r}; 冒烟未命中副本"
                    )
    finally:
        # 释放连接池句柄, 否则 Windows 上副本文件无法被测试清理(R6-07 同类问题)
        global_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="隔离应用冒烟(验证副本可被应用读取)")
    parser.add_argument("--database", required=True, help="待冒烟的副本(绝对路径)")
    parser.add_argument("--expect-key", default=None, help="断言该 app_setting key 存在")
    parser.add_argument(
        "--expect-value",
        default=None,
        help="与 --expect-key 配合, 断言其明文值等于该值(仅非敏感项)",
    )
    args = parser.parse_args()

    # 在 import 任何 backend.* 之前设置, 使全局 engine 也指向副本(配置 import 时固化)
    db_url = f"sqlite:///{Path(args.database).resolve().as_posix()}"
    os.environ["DATABASE_URL"] = db_url
    os.environ["SCHEDULER_ENABLED"] = "false"

    try:
        run_smoke(args.database, args.expect_key, args.expect_value)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 冒烟失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"[PASS] 隔离应用冒烟通过(副本 {args.database} 可被应用读取)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
