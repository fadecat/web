# -*- coding: utf-8 -*-
"""策略配置与评级目录合同测试(P2-R02/R04/R07)。

覆盖:
- "AAA" 字符串不会被保存成 ["A"](P2-R02 核心反例)
- ratings 非字符串数组/含空串 → 422
- NONE 只表示缺失评级; 空数组 = 不限
- 评级目录含规范 14 项 + 快照发现的未知值
- 旧配置 excluded_ratings 兼容只在读取时生效
不访问真实数据源。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app

BASE = "/api/cb-list"


@pytest.fixture()
def client(db):
    """TestClient + 线程安全内存库(TestClient 在独立线程发请求)。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.models.database import Base, get_db
    from backend.models import app_setting, data_status, valuation  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    # override 必须是函数: FastAPI 0.141 把类当依赖分析会生成 local_kw 必填参数
    app.dependency_overrides[get_db] = lambda: TestSession()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def thread_db(client):
    """与 client 同一内存库的会话(供测试写数据)。"""
    from backend.models.database import get_db

    session = app.dependency_overrides[get_db]()
    yield session
    session.close()


@pytest.fixture()
def tmp_factors(monkeypatch):
    """factors.json 指到临时文件, 测试互不影响且不写真实 data/。

    Windows 下 pytest tmp_path 基目录偶发 PermissionError(见 conftest 注释),
    改用 DATA_DIR 下临时目录。"""
    import pathlib
    import shutil
    import tempfile

    from backend.config import DATA_DIR
    from backend.services import cb_factors

    d = pathlib.Path(tempfile.mkdtemp(dir=str(DATA_DIR), prefix=".test_factors_"))
    f = d / "factors.json"
    monkeypatch.setattr(cb_factors, "FACTORS_PATH", f)
    yield f
    shutil.rmtree(d, ignore_errors=True)


def _tmpl(**overrides):
    base = {
        "id": "t1", "name": "测试策略",
        "target_count": 5, "hold_tolerance": 0,
        "exclusion_rules": [], "strategy_factors": [],
        "excluded_redeem_icons": [], "redeem_safe_days": 2,
        "excluded_bond_codes": [], "min_listing_days": 0,
        "ratings": ["AAA", "AA"],
    }
    base.update(overrides)
    return base


class TestSaveFactorsContract:
    def test_string_rating_not_split_to_chars(self, client, tmp_factors):
        """P2-R02 核心反例: ratings:"AAA" 必须 422, 不能被按字符迭代存成 ["A"]。"""
        r = client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings="AAA")],
        })
        assert r.status_code == 422

    def test_object_rating_rejected(self, client, tmp_factors):
        r = client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings={"AAA": True})],
        })
        assert r.status_code == 422

    def test_empty_string_rating_rejected(self, client, tmp_factors):
        r = client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings=["AA", "  "])],
        })
        assert r.status_code == 422

    def test_valid_ratings_saved(self, client, tmp_factors):
        r = client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings=["aa+", "NONE"])],
        })
        assert r.status_code == 200
        saved = r.json()["data"]["templates"][0]["ratings"]
        assert saved == ["AA+", "NONE"]  # 大写规范化; 写盘前由 _normalize_templates 排序

    def test_empty_ratings_means_unrestricted(self, client, tmp_factors):
        r = client.post(f"{BASE}/factors", json={
            "active_id": "t1", "templates": [_tmpl(ratings=[])],
        })
        assert r.status_code == 200
        assert r.json()["data"]["templates"][0]["ratings"] == []

    def test_missing_templates_rejected(self, client, tmp_factors):
        r = client.post(f"{BASE}/factors", json={"active_id": "t1"})
        assert r.status_code == 422


class TestReadConfigLegacy:
    def test_legacy_excluded_ratings_inverted_on_read(self, tmp_factors, monkeypatch):
        """旧配置(排除语义)只在读取迁移时反转, 不用于新 POST。"""
        from backend.services import cb_factors

        legacy = {
            "version": 1, "active_id": "t1",
            "templates": [_tmpl(ratings=None, excluded_ratings=["BB"])],
        }
        tmp_factors.write_text(json.dumps(legacy), encoding="utf-8")
        cfg = cb_factors.read_config()
        ratings = cfg["templates"][0]["ratings"]
        assert "BB" not in ratings
        assert "AAA" in ratings  # 反转后保留其余


class TestRatingCatalog:
    def test_catalog_contains_14_standard_entries(self, client):
        r = client.get(f"{BASE}/factors/ratings")
        assert r.status_code == 200
        catalog = r.json()
        values = [e["value"] for e in catalog]
        assert len(values) == 14
        assert values[:3] == ["AAA", "AA+", "AA"]
        none_entry = next(e for e in catalog if e["value"] == "NONE")
        assert none_entry["is_missing"] is True

    def test_catalog_discovers_unknown_values(self, client, thread_db):
        """快照中出现未登记评级(如 BB+)时出现在目录末尾。"""
        from datetime import date

        from backend.models.valuation import CbDailySnapshot

        thread_db.add(CbDailySnapshot(
            trade_date=date(2026, 9, 8), bond_id="110099", bond_nm="新债",
            rating_cd="BB+", price=100.0,
        ))
        thread_db.commit()
        r = client.get(f"{BASE}/factors/ratings")
        assert r.status_code == 200
        values = [e["value"] for e in r.json()]
        assert "BB+" in values
        assert values[-1] == "BB+"  # 未知值排末尾
