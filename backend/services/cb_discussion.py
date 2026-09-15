# -*- coding: utf-8 -*-
"""可转债「相关讨论」按需代理(集思录详情页, 不落库)。

数据形态: 详情页 #tbl_questions .question_list 内的帖子列表, 每条含
标题 / 相对链接(/question/{id}) / 回复数 / 浏览数 / 日期。

设计要点:
- 复用 jisilu 落盘会话(get_cookie 内部探活+按需重登, 本模块不新增登录);
- 进程内缓存 TTL 24h(讨论帖低频变化), 仅缓存成功结果;
- 批量预热前单线程取一次 cookie 再并发拉取(避免 N 次探活与并发重复登录),
  并发 4 路, 单只失败跳过(悬浮时惰性兜底);
- 详情页若返回登录页(cookie 中途失效), 重新取 cookie 重试一次。
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from backend.services import jisilu
from backend.services.jisilu_gateway import gateway

_DETAIL_URL = "https://www.jisilu.cn/data/convert_bond_detail/{bond_id}"
_BASE = "https://www.jisilu.cn"
# 登录页标记(匿名访问详情页会被重定向到登录页)
_LOGIN_MARKER = "帐号密码登录"

_CACHE_TTL_SECONDS = 24 * 3600
_MAX_BATCH = 100
_FETCH_CONCURRENCY = 4
_FETCH_TIMEOUT = 10

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()

# 帖子条目:
# <div class="title"><a target="_blank" href="/question/525018">标题</a></div>
# <div class="text"><span class="num">11条回复</span><span class="num">2089次浏览</span> <span>2026-09-04</span></div>
_ITEM_RE = re.compile(
    r'<div class="title">\s*<a[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>[^<]+)</a>\s*</div>\s*'
    r'<div class="text">\s*<span class="num">(?P<replies>[^<]*)</span>\s*'
    r'<span class="num">(?P<views>[^<]*)</span>\s*(?:<span>(?P<date>[^<]*)</span>)?',
    re.S,
)


def _validate_bond_id(bond_id: str) -> str:
    """转债代码必须是 6 位数字(拼 URL 前先收口, 拒绝任意输入)。"""
    if not re.fullmatch(r"\d{6}", bond_id or ""):
        raise ValueError("转债代码须为 6 位数字")
    return bond_id


def _parse_discussions(html: str) -> list[dict]:
    """从详情页 HTML 提取「相关讨论」帖子列表; 无该板块返回空。"""
    start = html.find('id="tbl_questions"')
    if start < 0:
        return []
    end = html.find('id="tbl_bonds"', start)
    seg = html[start:end if end > 0 else len(html)]
    items = []
    for m in _ITEM_RE.finditer(seg):
        href = m.group("href").strip()
        if href.startswith("/"):
            href = _BASE + href
        items.append({
            "title": m.group("title").strip(),
            "url": href,
            "replies": m.group("replies").strip(),
            "views": m.group("views").strip(),
            "date": (m.group("date") or "").strip(),
        })
    return items


def _fetch_detail(bond_id: str, cookie: str) -> str:
    """用给定 cookie 拉详情页 HTML(cookie 由调用方单线程获取)。"""
    resp = gateway.request("GET", _DETAIL_URL.format(bond_id=bond_id), headers={"User-Agent": jisilu.LOGIN_HEADERS["User-Agent"], "Cookie": cookie}, timeout=_FETCH_TIMEOUT, follow_redirects=True, request_type="page")
    resp.raise_for_status()
    return resp.text


def _fetch_with_retry(bond_id: str) -> str:
    """拉详情页; 命中登录页说明 cookie 失效, 重取 cookie 重试一次。"""
    return gateway.request("GET", _DETAIL_URL.format(bond_id=bond_id), timeout=_FETCH_TIMEOUT, request_type="page").text


def get_discussions(bond_id: str) -> list[dict]:
    """单只转债的相关讨论列表(带缓存); bond_id 非 6 位数字抛 ValueError。"""
    bond_id = _validate_bond_id(bond_id)
    with _cache_lock:
        hit = _cache.get(bond_id)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    items = _parse_discussions(_fetch_with_retry(bond_id))
    with _cache_lock:
        _cache[bond_id] = (time.monotonic(), items)
    return items


def get_discussions_batch(bond_ids: list[str]) -> dict[str, list[dict]]:
    """批量预热: 先单线程取一次 cookie, 再并发拉未命中缓存的债券。

    单只失败跳过(不中断整批), 返回成功取得的 {bond_id: items}。
    """
    ids: list[str] = []
    for b in bond_ids:
        b = _validate_bond_id(b)
        if b not in ids:
            ids.append(b)
    if not 1 <= len(ids) <= _MAX_BATCH:
        raise ValueError(f"单次批量须 1-{_MAX_BATCH} 只(去重后)")

    def _fresh(b: str) -> bool:
        with _cache_lock:
            hit = _cache.get(b)
            return bool(hit) and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS

    missing = [b for b in ids if not _fresh(b)]
    if missing:
        # 先单线程取 cookie(探活/重登只发生一次), 线程内直接复用
        client = gateway.lease()
        cookie = ""

        def _work(b: str) -> None:
            try:
                html = client.request("GET", _DETAIL_URL.format(bond_id=b), timeout=_FETCH_TIMEOUT, request_type="page").text
                items = _parse_discussions(html)
                with _cache_lock:
                    _cache[b] = (time.monotonic(), items)
            except Exception:  # noqa: BLE001 单只失败跳过, 悬浮惰性兜底
                pass

        try:
            with ThreadPoolExecutor(max_workers=_FETCH_CONCURRENCY) as ex:
                list(ex.map(_work, missing))
        finally:
            client.close()

    with _cache_lock:
        return {b: _cache[b][1] for b in ids if b in _cache}
