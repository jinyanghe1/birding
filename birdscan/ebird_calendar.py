"""eBird 观鸟日历：拉取社区热点数据，生成推荐清单。

数据源：
  - eBird API v2（只读）：按地区或坐标查询最近观测
  - 本地观测记录：我的观测
  - eBird 本地化最近观测：物种中文名

注意：eBird 历史观测接口对北京返回空，地理范围历史观测不支持，
所以用「最近 30 天观测」代替「历史全年数据」。

用法：
    python -m birdscan.cli ebird calendar --recent  # 拉取实时鸟讯
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from . import config, store

log = logging.getLogger("birdscan")

TOKEN_FILE = Path(config.DATA_DIR) / ".ebird_token"
CACHE_DIR = Path(config.DATA_DIR) / "cache"
RECENT_CACHE_TTL = 6 * 60 * 60
TAXONOMY_CACHE_TTL = 30 * 24 * 60 * 60
ZH_LOCALE = "zh_SIM"
ZH_CACHE_FILE = "ebird_taxonomy_zh_SIM.json"

# 北京地区码（实测确认：CN-BJ 返回空，CN-11 有效）
BEIJING_REGION = "CN-11"
DEFAULT_REGION = "CN-44"


def _get_token() -> str | None:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    import os
    return os.environ.get("EBIRD_API_TOKEN")


def _req(url: str) -> list | dict | None:
    token = _get_token()
    if not token:
        log.error("未设置 eBird API token")
        return None
    req = urllib.request.Request(
        url, headers={"x-ebirdapitoken": token, "User-Agent": "birdscan/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log.error("eBird 请求失败 %s: %s", url, e)
        return None


def _read_cache(path: Path) -> list | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        if path.exists():
            log.warning("读取缓存失败 %s: %s", path, e)
        return None
    return data if isinstance(data, list) else None


def _cache_is_fresh(path: Path, ttl: int) -> bool:
    try:
        return time.time() - path.stat().st_mtime < ttl
    except OSError:
        return False


def _write_cache(path: Path, data: list) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        log.info("缓存到 %s", path)
    except OSError as e:
        log.warning("写入缓存失败 %s: %s", path, e)


def _recent_cache_file(region: str | None, lat: float | None, lon: float | None,
                       days: int) -> Path:
    if region is not None:
        safe_region = re.sub(r"[^A-Za-z0-9.-]", "_", region)
        key = f"region_{safe_region}"
    else:
        key = f"nearby_{lat:.4f}_{lon:.4f}"
    return CACHE_DIR / f"recent_{key}_{days}d.json"


def _aggregate_recent(rows: list[dict]) -> list[dict]:
    by_species = defaultdict(lambda: {"count": 0, "locations": set(), "dates": set()})
    for r in rows:
        sc = r["speciesCode"]
        by_species[sc]["count"] += 1
        by_species[sc]["locations"].add(r["locName"])
        by_species[sc]["dates"].add(r["obsDt"][:10])
        by_species[sc]["comName"] = r["comName"]
        by_species[sc]["sciName"] = r["sciName"]

    return [
        {"speciesCode": sc, "comName": v["comName"], "sciName": v["sciName"],
         "count": v["count"], "locations": sorted(v["locations"]),
         "dates": sorted(v["dates"])}
        for sc, v in sorted(by_species.items(), key=lambda x: -x[1]["count"])
    ]


def _recent_url(region: str | None, lat: float | None, lon: float | None,
                days: int, spp_locale: str | None = None) -> str:
    if region is not None:
        url = f"https://api.ebird.org/v2/data/obs/{region}/recent?back={days}"
    else:
        url = ("https://api.ebird.org/v2/data/nearby/obs/recent"
               f"?lat={lat}&lng={lon}&dist=50&back={days}")
    if spp_locale:
        url += f"&sppLocale={spp_locale}"
    return url


def _fetch_recent(region: str | None, lat: float | None, lon: float | None,
                  days: int) -> list[dict]:
    """拉取并聚合最近观测；缓存 6 小时，请求失败时使用陈旧缓存。"""
    cache_file = _recent_cache_file(region, lat, lon, days)
    cached = _read_cache(cache_file)
    if cached is not None and _cache_is_fresh(cache_file, RECENT_CACHE_TTL):
        log.info("读缓存：%s", cache_file)
        return cached

    rows = _req(_recent_url(region, lat, lon, days))
    if isinstance(rows, list):
        out = _aggregate_recent(rows)
        _write_cache(cache_file, out)
        return out
    if cached is not None:
        log.warning("eBird 请求失败，使用陈旧缓存：%s", cache_file)
        return cached
    return []


def fetch_recent_beijing(days: int = 30) -> list[dict]:
    """拉取北京最近观测（CN-11 地区码）。"""
    return _fetch_recent(BEIJING_REGION, None, None, days)


def _index_zh_names(rows: list[dict]) -> dict[str, dict[str, str]]:
    names = {"sci": {}, "en": {}}
    for row in rows:
        name_zh = row.get("comName")
        if not name_zh:
            continue
        if row.get("sciName"):
            names["sci"][row["sciName"]] = name_zh
        # 部分分类数据会额外携带英文俗名；有则建立英文名索引。
        name_en = row.get("comNameEn") or row.get("comName_en")
        if name_en:
            names["en"][name_en.lower()] = name_zh
    return names


def _get_zh_names(localized_url: str, fallback_url: str | None = None,
                  required_sci_names: set[str] | None = None
                  ) -> dict[str, dict[str, str]]:
    """以简体中文最近观测建立中文名索引，失败时可回退繁体及陈旧缓存。"""
    cache_file = CACHE_DIR / ZH_CACHE_FILE
    cached = _read_cache(cache_file)
    cache_fresh = cached is not None and _cache_is_fresh(
        cache_file, TAXONOMY_CACHE_TTL)
    names = _index_zh_names(cached or [])
    required_sci_names = required_sci_names or set()
    missing = any(sci not in names["sci"] for sci in required_sci_names)

    if not cache_fresh or missing:
        localized = _req(localized_url)
        if not isinstance(localized, list) and fallback_url:
            localized = _req(fallback_url)
        if isinstance(localized, list):
            # 合并其他地区的缓存，新数据优先覆盖同一学名的旧名称。
            rows = (cached or []) + localized
            _write_cache(cache_file, rows)
            names = _index_zh_names(rows)
        elif cached is not None:
            log.warning("eBird 中文名请求失败，使用缓存：%s", cache_file)
    return names


def _chinese_name(sp: dict, zh_names: dict[str, dict[str, str]],
                  en_to_cn: dict[str, str]) -> str:
    name_en = sp["comName"]
    return (zh_names["sci"].get(sp["sciName"])
            or zh_names["en"].get(name_en.lower())
            or en_to_cn.get(name_en.lower())
            or name_en)


def get_recommendations(region: str | None = None, lat: float | None = None,
                        lon: float | None = None,
                        days: int = 30) -> list[dict]:
    """推荐清单：社区热点物种 - 我的观测 = 这个月该去看什么。

    地区码优先；未传地区码且经纬度齐全时查询附近 50 公里。
    返回 [{"species": "...", "species_en": "...", "sciName": "...",
            "community_count": 15, "my_count": 0, "locations": [...],
            "dates": [...], "priority": "high"}, ...]
    """
    use_nearby = region is None and lat is not None and lon is not None
    if not use_nearby:
        region = region or DEFAULT_REGION

    # 我的观测（按物种聚合，带英文名映射）
    with store.conn_ctx(readonly=True) as con:
        my_rows = con.execute(
            """SELECT s.common_name_cn, s.common_name_en, COUNT(*) c
               FROM observations o JOIN species s ON s.id = o.species_id
               WHERE o.confidence >= ? AND s.in_china = 1
               GROUP BY s.common_name_cn""", (0.45,)).fetchall()
    # 建立英文名 -> 中文名映射
    en_to_cn = {}
    my_cn_set = set()
    my_en_map = {}
    for r in my_rows:
        my_cn_set.add(r["common_name_cn"])
        if r["common_name_en"]:
            en_to_cn[r["common_name_en"].lower()] = r["common_name_cn"]
            my_en_map[r["common_name_en"].lower()] = r["c"]

    if use_nearby:
        community = _fetch_recent(None, lat, lon, days)
    else:
        community = _fetch_recent(region, None, None, days)
    if community:
        location_args = (region if not use_nearby else None, lat, lon, days)
        zh_url = _recent_url(*location_args, spp_locale=ZH_LOCALE)
        zh_fallback_url = _recent_url(*location_args, spp_locale="zh")
        zh_names = _get_zh_names(
            zh_url, fallback_url=zh_fallback_url,
            required_sci_names={sp["sciName"] for sp in community})
    else:
        zh_names = {"sci": {}, "en": {}}

    recommendations = []
    for sp in community:
        name_en = sp["comName"]
        name_cn = _chinese_name(sp, zh_names, en_to_cn)
        my_count = my_en_map.get(name_en.lower(), 0)
        if my_count == 0 and name_cn in my_cn_set:
            # 中文名直接匹配（处理英文名缺失的情况）
            my_count = next((r["c"] for r in my_rows
                            if r["common_name_cn"] == name_cn), 0)

        if my_count == 0:  # 我没看过的
            recommendations.append({
                "species": name_cn,
                "species_en": name_en,
                "sciName": sp["sciName"],
                "community_count": sp["count"],
                "my_count": my_count,
                "locations": sp["locations"][:3],
                "dates": sp["dates"][:3],
                "priority": "high" if sp["count"] >= 5 else "medium",
            })

    # 按社区观测次数排序
    recommendations.sort(key=lambda x: -x["community_count"])
    return recommendations


def get_seasonal_pattern() -> dict:
    """留鸟/旅鸟/冬夏候鸟标记（基于我的观测数据）。

    返回 {"留鸟": [...], "旅鸟": [...], "冬候鸟": [...], "夏候鸟": [...]}
    """
    with store.conn_ctx(readonly=True) as con:
        rows = con.execute(
            """SELECT s.common_name_cn,
                      CAST(substr(o.obs_date, 6, 2) AS INT) month,
                      COUNT(*) c
               FROM observations o JOIN species s ON s.id = o.species_id
               WHERE o.confidence >= 0.45 AND s.in_china = 1
               GROUP BY s.common_name_cn, month""").fetchall()

    by_species = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_species[r["common_name_cn"]][r["month"]] = r["c"]

    out = {"留鸟": [], "旅鸟": [], "冬候鸟": [], "夏候鸟": []}
    for name, months in by_species.items():
        month_set = set(months.keys())
        if len(month_set) >= 10:
            out["留鸟"].append(name)
        elif all(m in (11, 12, 1, 2) for m in month_set):
            out["冬候鸟"].append(name)
        elif all(m in (5, 6, 7, 8) for m in month_set):
            out["夏候鸟"].append(name)
        else:
            out["旅鸟"].append(name)

    return {k: sorted(v) for k, v in out.items()}
