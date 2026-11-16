"""eBird 推荐单元测试：HTTP 全部打桩，不依赖网络。"""
import os
import sqlite3
import time
from contextlib import contextmanager

import pytest

from birdscan import ebird_calendar as ec


def _obs(code="bird-a", en="Bird A", sci="Birdus a",
         dt="2026-11-01 08:00", location="测试公园"):
    return {
        "speciesCode": code,
        "comName": en,
        "sciName": sci,
        "obsDt": dt,
        "locName": location,
    }


def _observe(db_path, name_cn, name_en):
    con = sqlite3.connect(db_path)
    cur = con.execute(
        "INSERT INTO species(common_name_cn, common_name_en, in_china) VALUES(?, ?, 1)",
        (name_cn, name_en),
    )
    con.execute(
        "INSERT INTO observations(species_id, confidence) VALUES(?, 0.9)",
        (cur.lastrowid,),
    )
    con.commit()
    con.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(ec, "CACHE_DIR", cache_dir)

    db_path = tmp_path / "birds.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE species (
            id INTEGER PRIMARY KEY,
            common_name_cn TEXT,
            common_name_en TEXT,
            in_china INTEGER
        );
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY,
            species_id INTEGER,
            confidence REAL
        );
        """
    )
    con.close()

    @contextmanager
    def conn_ctx(readonly=False):
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    monkeypatch.setattr(ec.store, "conn_ctx", conn_ctx)
    return db_path


def test_region_and_nearby_endpoint_selection(env, monkeypatch):
    calls = []

    def fake_req(url):
        calls.append(url)
        return [_obs()]

    monkeypatch.setattr(ec, "_req", fake_req)

    result = ec.get_recommendations(days=14)
    assert f"https://api.ebird.org/v2/data/obs/{ec.DEFAULT_REGION}/recent?back=14" in calls
    assert any("sppLocale=zh_SIM" in url for url in calls)
    assert not any("/ref/taxonomy/" in url for url in calls)
    assert {"species", "species_en", "sciName", "community_count", "my_count",
            "locations", "dates", "priority"} <= result[0].keys()

    calls.clear()
    ec.get_recommendations(lat=22.5431, lon=114.0579, days=7)
    assert ("https://api.ebird.org/v2/data/nearby/obs/recent"
            "?lat=22.5431&lng=114.0579&dist=50&back=7") in calls

    calls.clear()
    ec.get_recommendations(region="CN-11", lat=1.0, lon=2.0, days=3)
    assert "https://api.ebird.org/v2/data/obs/CN-11/recent?back=3" in calls
    assert not any("/nearby/" in url for url in calls)

    nearby_cache = ec._recent_cache_file(None, 22.54311, 114.05791, 7)
    assert nearby_cache.name == "recent_nearby_22.5431_114.0579_7d.json"


def test_recent_cache_ttl_and_stale_fallback(env, monkeypatch):
    calls = []
    payload = [_obs()]

    def fake_req(url):
        calls.append(url)
        return payload

    monkeypatch.setattr(ec, "_req", fake_req)
    first = ec._fetch_recent("CN-11", None, None, 7)
    assert first[0]["count"] == 1

    # 新鲜缓存不再请求网络。
    assert ec._fetch_recent("CN-11", None, None, 7) == first
    assert len(calls) == 1
    cache_file = env.parent / "cache" / "recent_region_CN-11_7d.json"
    assert cache_file.exists()

    # 超过 6 小时重新拉取并更新缓存。
    expired = time.time() - ec.RECENT_CACHE_TTL - 1
    os.utime(cache_file, (expired, expired))
    payload = [_obs(), _obs(dt="2026-11-02 09:00", location="另一个公园")]
    refreshed = ec._fetch_recent("CN-11", None, None, 7)
    assert refreshed[0]["count"] == 2
    assert len(calls) == 2

    # 过期后请求失败，返回陈旧缓存；缓存也没有时返回空列表。
    expired = time.time() - ec.RECENT_CACHE_TTL - 1
    os.utime(cache_file, (expired, expired))
    monkeypatch.setattr(ec, "_req", lambda url: None)
    assert ec._fetch_recent("CN-11", None, None, 7) == refreshed
    cache_file.unlink()
    assert ec._fetch_recent("CN-11", None, None, 7) == []


def test_chinese_name_fallback_chain(env, monkeypatch):
    rows = [
        _obs(code="a", en="English A", sci="A a"),
        _obs(code="b", en="English B", sci="B b"),
        _obs(code="c", en="English C", sci="C c"),
    ]
    taxonomy = [
        {"sciName": "A a", "comName": "中文甲"},
        {"sciName": "Other", "comName": "中文乙", "comNameEn": "English B"},
    ]

    def fake_req(url):
        if "sppLocale=zh_SIM" in url:
            return taxonomy
        return rows

    monkeypatch.setattr(ec, "_req", fake_req)
    result = ec.get_recommendations()
    names = {item["species_en"]: item["species"] for item in result}
    assert names == {
        "English A": "中文甲",  # 中文分类：按学名
        "English B": "中文乙",  # 中文分类：按英文名（数据提供时）
        "English C": "English C",  # 最后回退到英文名
    }
    assert ec._chinese_name(
        {"sciName": "D d", "comName": "Local Bird"},
        {"sci": {}, "en": {}},
        {"local bird": "本地中文"},
    ) == "本地中文"  # 中间回退：本地名录


def test_simplified_recent_names_are_cached(env, monkeypatch):
    def fake_req(url):
        if "sppLocale=zh_SIM" in url:
            return [_obs(en="大山雀")]
        return [_obs(en="Asian Tit")]

    monkeypatch.setattr(ec, "_req", fake_req)
    result = ec.get_recommendations(region="CN-44")
    assert len(result) == 1
    assert result[0]["species"] == "大山雀"
    assert result[0]["species_en"] == "Asian Tit"

    # 近期观测与中文名缓存均新鲜时，不再发起任何请求。
    monkeypatch.setattr(ec, "_req", lambda url: pytest.fail(f"意外请求：{url}"))
    assert ec.get_recommendations(region="CN-44") == result


def test_simplified_failure_falls_back_to_traditional(env, monkeypatch):
    def fake_req(url):
        if "sppLocale=zh_SIM" in url:
            return None
        if "sppLocale=zh" in url:
            return [_obs(en="白頰山雀")]
        return [_obs(en="Oriental Magpie-Robin")]

    monkeypatch.setattr(ec, "_req", fake_req)
    result = ec.get_recommendations(region="CN-44")
    assert result[0]["species"] == "白頰山雀"
    assert result[0]["species_en"] == "Oriental Magpie-Robin"


def test_chinese_name_failure_does_not_break_recommendations(env, monkeypatch):
    def fake_req(url):
        return None if "sppLocale=zh" in url else [_obs(en="Fallback Bird")]

    monkeypatch.setattr(ec, "_req", fake_req)
    result = ec.get_recommendations(region="CN-44")
    assert len(result) == 1
    assert result[0]["species"] == "Fallback Bird"


def test_user_observed_species_are_excluded(env, monkeypatch):
    _observe(env, "已看鸟", "Already Seen")
    _observe(env, "中文已看", None)
    rows = [
        _obs(code="seen-en", en="Already Seen", sci="A a"),
        _obs(code="seen-cn", en="Community Name", sci="B b"),
        _obs(code="new", en="New Bird", sci="C c"),
    ]
    taxonomy = [{"sciName": "B b", "comName": "中文已看"}]

    def fake_req(url):
        if "sppLocale=zh_SIM" in url:
            return taxonomy
        return rows

    monkeypatch.setattr(ec, "_req", fake_req)
    result = ec.get_recommendations()
    assert [item["species_en"] for item in result] == ["New Bird"]
    assert result[0]["my_count"] == 0
