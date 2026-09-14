# -*- coding: utf-8 -*-
"""集思录统一认证层。

移植自 market-daily/src/common/jisilu.py,核心逻辑不变:
- AES-ECB 加密账密登录
- cookie 落盘持久化(data/state/jisilu_session.json)
- 下次先探活(首页登录态标记),失效才重新登录

节流保护(集思录会因「登录或者刷新过快」限制账号):
- 探活通过的 cookie 进程内缓存 10 分钟 —— 探活请求本身也计入刷新频率,
  批量抓取(如相关讨论预热)不应每只标的都打一次首页;
- 探活网络异常短重试一次,网络抖动不误判失效、不烧登录;
- 账密登录受「每日预算 + 失败冷却」双重闸门,超限抛 RuntimeError 次日自动恢复。

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
# 节流参数
# ---------------------------------------------------------------------------

_CACHE_TTL_SEC = 600        # 探活通过的 cookie 进程内缓存时长(探活也算刷新, 不必每次打首页)
_PROBE_RETRY_DELAY_SEC = 2  # 探活网络异常的短重试间隔
_LOGIN_COOLDOWN_SEC = 600   # 登录失败后的退避时长(防止紧密重试触发更严的限制)
_DAILY_LOGIN_BUDGET = 3     # 每日登录尝试上限(含失败), 保护账号额度

# 进程内缓存状态(get_cookie 读写; 测试需重置)
_cached_cookie = ""
_cached_at = 0.0            # time.monotonic() 读数
_last_login_fail_at = -1e9


def _login_count_file() -> Path:
    """每日登录计数落盘路径(与 session 同目录)。"""
    return SESSION_DIR / "jisilu_login_count.json"


def _read_login_state() -> tuple[str, int]:
    """读当日登录尝试计数; 跨天自动归零。返回 (今日日期, 已尝试次数)。"""
    today = datetime.now(ZoneInfo(_SESSION_TIMEZONE)).strftime("%Y-%m-%d")
    try:
        data = json.loads(_login_count_file().read_text(encoding="utf-8"))
        if data.get("date") == today:
            return today, int(data.get("count") or 0)
    except Exception:
        pass
    return today, 0


def _bump_login_state(today: str, count: int) -> None:
    """落盘当日登录尝试计数(失败不阻塞登录流程, 只告警)。"""
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        _login_count_file().write_text(
            json.dumps({"date": today, "count": count}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception:
        logger.warning("集思录登录计数落盘失败(不影响本次登录)")


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

def _probe_once(cookie_str: str) -> tuple[bool, bool]:
    """单次探活。返回 (cookie 是否仍有效, 是否网络/HTTP 异常)。"""
    headers = {
        "User-Agent": LOGIN_HEADERS["User-Agent"],
        "Cookie": cookie_str,
    }
    try:
        resp = httpx.get(HOME_URL, headers=headers, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        return _LOGGED_IN_MARKER in resp.text, False
    except Exception:
        logger.warning("集思录会话探活异常,按失效处理")
        return False, True


def _probe_cookie(cookie_str: str) -> bool:
    """探活 cookie:拉首页看登录态标记。

    网络/HTTP 异常(而非明确的未登录)短重试一次 ——
    抖动误判失效会白白消耗一次登录额度。
    """
    ok, network_error = _probe_once(cookie_str)
    if not ok and network_error:
        time.sleep(_PROBE_RETRY_DELAY_SEC)
        ok, _ = _probe_once(cookie_str)
    return ok


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

    三层节流, 避免触发集思录「登录或者刷新过快」限制:
    1. 进程内缓存: 探活通过的 cookie 在 TTL 内直接复用(探活请求本身计入刷新频率);
    2. 探活网络异常短重试一次, 不因网络抖动误判失效;
    3. 账密登录受每日预算与失败冷却约束, 超限抛 RuntimeError(次日自动恢复)。

    失败抛 RuntimeError。凭据从环境变量 JISILU_USERNAME / JISILU_PASSWORD 读取。
    """
    global _cached_cookie, _cached_at, _last_login_fail_at

    # 0) 进程内缓存: TTL 内直接复用, 不发探活请求
    now = time.monotonic()
    if _cached_cookie and now - _cached_at < _CACHE_TTL_SEC:
        return _cached_cookie

    # 1) 尝试复用落盘 cookie(一次探活往返)
    cached = _load_cached_cookie()
    if cached and _probe_cookie(cached):
        logger.info("复用落盘的集思录会话(免登录)")
        _cached_cookie, _cached_at = cached, now
        return cached

    # 2) 登录前的双重闸门: 失败冷却 + 每日预算
    if now - _last_login_fail_at < _LOGIN_COOLDOWN_SEC:
        wait_min = int((_LOGIN_COOLDOWN_SEC - (now - _last_login_fail_at)) // 60) + 1
        raise RuntimeError(f"集思录登录处于失败退避期, 约 {wait_min} 分钟后重试")
    today, count = _read_login_state()
    if count >= _DAILY_LOGIN_BUDGET:
        raise RuntimeError(
            f"今日集思录登录尝试已达 {count} 次(上限 {_DAILY_LOGIN_BUDGET}), "
            "为避免账号被进一步限制已暂停登录, 次日自动恢复"
        )

    username = (username or os.environ.get("JISILU_USERNAME", "")).strip()
    password = (password or os.environ.get("JISILU_PASSWORD", "")).strip()
    if not username or not password:
        raise RuntimeError(
            "集思录登录需要 JISILU_USERNAME / JISILU_PASSWORD 环境变量"
        )

    _bump_login_state(today, count + 1)  # 失败的尝试也计数(同样消耗账号耐心)
    if cached:
        logger.info("落盘的集思录会话已失效,重新账密登录(今日第 %d 次)", count + 1)
    else:
        logger.info("无落盘会话,首次登录(今日第 %d 次)", count + 1)

    cookie = _login(username, password)
    if not cookie:
        _last_login_fail_at = time.monotonic()
        raise RuntimeError(
            "集思录登录失败,请检查网络或 JISILU_USERNAME/JISILU_PASSWORD"
        )

    _save_cookie(cookie)
    _cached_cookie, _cached_at = cookie, time.monotonic()
    logger.info("集思录账密登录成功,会话 cookie 已落盘")
    return cookie


def invalidate_cookie(cookie_str: Optional[str] = None) -> None:
    """调用方发现 cookie 已失效(接口跳登录页/401)时主动清缓存。

    进程内 TTL 缓存期间 get_cookie 不会重新探活, 中途失效必须由调用方
    打这个标记, 下次 get_cookie 才会走探活/重登。
    """
    global _cached_cookie
    if cookie_str is None or cookie_str == _cached_cookie:
        _cached_cookie = ""


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
