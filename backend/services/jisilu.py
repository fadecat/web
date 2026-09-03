# -*- coding: utf-8 -*-
"""集思录统一认证层。

移植自 market-daily/src/common/jisilu.py,核心逻辑不变:
- AES-ECB 加密账密登录
- cookie 落盘持久化(data/state/jisilu_session.json)
- 下次先探活(首页登录态标记),失效才重新登录
- 避免消耗日登录额度

依赖: pycryptodome (Crypto.Cipher.AES)
环境变量: JISILU_USERNAME / JISILU_PASSWORD
"""
from __future__ import annotations

import binascii
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from backend.config import DATA_DIR

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError:
    AES = None
    pad = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

AES_KEY = "397151C04723421F"
LOGIN_URL = "https://www.jisilu.cn/webapi/account/login_process/"
HOME_URL = "https://www.jisilu.cn/"

LOGIN_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.jisilu.cn",
    "Referer": "https://www.jisilu.cn/account/login/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

# 首页 HTML 登录态标记:已登录页面有 <a href="https://www.jisilu.cn/logout/">退出</a>
_LOGGED_IN_MARKER = "jisilu.cn/logout/"
_SESSION_TIMEZONE = "Asia/Shanghai"

# cookie 落盘路径
SESSION_DIR = DATA_DIR / "state"
SESSION_FILE = SESSION_DIR / "jisilu_session.json"


# ---------------------------------------------------------------------------
# cookie 落盘 / 读取
# ---------------------------------------------------------------------------

def _load_cached_cookie() -> str:
    """从落盘文件读取 cookie 字符串。"""
    if not SESSION_FILE.exists():
        return ""
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        return str(data.get("cookie") or "")
    except Exception:
        return ""


def _save_cookie(cookie_str: str) -> None:
    """落盘 cookie 字符串。"""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "cookie": cookie_str,
        "saved_at": datetime.now(ZoneInfo(_SESSION_TIMEZONE)).isoformat(timespec="seconds"),
    }
    SESSION_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 探活
# ---------------------------------------------------------------------------

def _probe_cookie(cookie_str: str) -> bool:
    """探活 cookie:拉首页看登录态标记。任何异常按失效处理。"""
    if not cookie_str:
        return False
    headers = {
        "User-Agent": LOGIN_HEADERS["User-Agent"],
        "Cookie": cookie_str,
    }
    try:
        resp = httpx.get(HOME_URL, headers=headers, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        return _LOGGED_IN_MARKER in resp.text
    except Exception:
        logger.warning("集思录会话探活异常,按失效处理")
        return False


# ---------------------------------------------------------------------------
# AES 加密 + 登录
# ---------------------------------------------------------------------------

def jslencode(text: str) -> str:
    """集思录登录接口要求的 AES-ECB(hex) 加密。"""
    if AES is None or pad is None:
        raise RuntimeError("缺少 pycryptodome 依赖,请先执行: pip install pycryptodome")
    key = AES_KEY.encode("utf-8")
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))
    return binascii.hexlify(encrypted).decode("utf-8")


def _login(username: str, password: str) -> str:
    """账密登录集思录,返回 cookie 字符串;失败返回空串。"""
    data = {
        "return_url": "https://www.jisilu.cn/",
        "user_name": jslencode(username),
        "password": jslencode(password),
        "auto_login": "1",
        "aes": "1",
    }
    try:
        resp = httpx.post(LOGIN_URL, headers=LOGIN_HEADERS, data=data, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 200:
            logger.error("集思录登录失败(账密): %s", result.get("msg", "未知错误"))
            return ""
        # 从 Set-Cookie 头拼装 cookie 字符串
        cookies = resp.cookies
        if cookies:
            return "; ".join(f"{k}={v}" for k, v in cookies.items())
        # 有些版本 cookie 在后续请求的 jar 里,尝试从 response headers 提取
        set_cookie = resp.headers.get("set-cookie", "")
        if set_cookie:
            parts = []
            for item in set_cookie.split(","):
                item = item.strip()
                if "=" in item:
                    pair = item.split(";")[0].strip()
                    if pair and "=" in pair:
                        parts.append(pair)
            if parts:
                return "; ".join(parts)
        logger.error("集思录登录成功但未获取到 Cookie")
        return ""
    except Exception as exc:
        logger.exception("集思录登录异常: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def get_cookie(username: Optional[str] = None, password: Optional[str] = None) -> str:
    """返回可用的集思录 cookie 字符串。

    优先复用落盘会话(探活通过才用);失效/不存在才账密登录,成功后落盘。
    失败抛 RuntimeError。

    凭据从环境变量 JISILU_USERNAME / JISILU_PASSWORD 读取。
    """
    username = (username or os.environ.get("JISILU_USERNAME", "")).strip()
    password = (password or os.environ.get("JISILU_PASSWORD", "")).strip()

    # 1) 尝试复用落盘 cookie
    cached = _load_cached_cookie()
    if cached and _probe_cookie(cached):
        logger.info("复用落盘的集思录会话(免登录)")
        return cached

    if cached:
        logger.info("落盘的集思录会话已失效,重新账密登录")
    else:
        logger.info("无落盘会话,首次登录")

    # 2) 账密登录
    if not username or not password:
        raise RuntimeError(
            "集思录登录需要 JISILU_USERNAME / JISILU_PASSWORD 环境变量"
        )

    cookie = _login(username, password)
    if not cookie:
        raise RuntimeError(
            "集思录登录失败,请检查网络或 JISILU_USERNAME/JISILU_PASSWORD"
        )

    _save_cookie(cookie)
    logger.info("集思录账密登录成功,会话 cookie 已落盘")
    return cookie


def get_auth_headers() -> dict[str, str]:
    """返回带登录 cookie 的 HTTP headers,供 fetcher 使用。

    自动复用/登录,调用方只需:
        headers = {**DEFAULT_HEADERS, **get_auth_headers()}
    """
    cookie = get_cookie()
    return {"Cookie": cookie}


def fetch_with_auth(url: str, *, headers: dict | None = None, **kwargs) -> httpx.Response:
    """带集思录登录态的 HTTP GET。

    自动注入 Cookie,调用方不需手动处理登录。
    """
    auth_headers = {
        "User-Agent": LOGIN_HEADERS["User-Agent"],
        "Cookie": get_cookie(),
    }
    if headers:
        auth_headers.update(headers)
    return httpx.get(url, headers=auth_headers, follow_redirects=True, **kwargs)
