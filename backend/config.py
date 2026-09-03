# -*- coding: utf-8 -*-
"""应用配置。

使用 pydantic-settings 从环境变量 / .env 加载,并提供便捷的路径常量。
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录 = 本文件向上两级 (backend/config.py -> web/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 主动加载 .env 到 os.environ,让非 pydantic 的代码(如 jisilu.py)也能读到
load_dotenv(str(PROJECT_ROOT / ".env"))


class Settings(BaseSettings):
    """运行时配置。字段可通过同名环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 运行环境
    env: str = "development"
    log_level: str = "INFO"

    # 数据库
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'web.db').as_posix()}"

    # API 监听
    host: str = "0.0.0.0"
    port: int = 8000

    # 调度开关
    scheduler_enabled: bool = False


settings = Settings()

# 常用目录常量
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
