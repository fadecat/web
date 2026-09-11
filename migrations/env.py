"""Alembic 迁移环境(第四阶段/T5).

安全约束:
1. 必须显式提供数据库 URL: 优先 `-x database_url=...` CLI 参数,
   其次 alembic.ini 的 sqlalchemy.url; 两者都缺或仍是占位符时直接失败,
   防止误对日常库(settings 默认的 data/web.db)执行迁移。
2. 绑定完整 ORM metadata: 全部业务模型在导入时注册到 Base.metadata。
3. compare_type=True + SQLite render_as_batch, 支持结构差异比较。
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.models.database import Base
from backend.models import app_setting, data_status, jisilu_stock, valuation  # noqa: F401

config = context.config
target_metadata = Base.metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 显式 URL: -x database_url=... 优先, 其次 ini; 占位符/缺失即失败
x_args = context.get_x_argument(as_dictionary=True)
database_url = x_args.get("database_url") or config.get_main_option("sqlalchemy.url")
if not database_url or "__explicit_url_required__" in database_url:
    raise RuntimeError("必须显式提供 Alembic database_url(用 -x database_url=sqlite:///... )")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    """离线模式: 不连接数据库, 生成 SQL 脚本。"""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式: 连接目标库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
