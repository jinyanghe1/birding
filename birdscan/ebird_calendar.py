"""eBird 观鸟日历：拉取社区热点数据，生成推荐清单。

数据源：
  - eBird API v2（只读）：北京最近观测（CN-11 地区码）
  - 本地观测记录：我的观测
  - 中国鸟类名录：物种元数据

注意：eBird 历史观测接口对北京返回空，地理范围历史观测不支持，
所以用「最近 30 天观测」代替「历史全年数据」。

用法：
    python -m birdscan.cli ebird calendar --recent  # 拉取实时鸟讯
"""
from __future__ import annotations

import json
import logging
import urllib.request
from collections import defaultdict
from pathlib import Path

from . import config, store

log = logging.getLogger("birdscan")

TOKEN_FILE = Path(config.DATA_DIR) / ".ebird_token"
CACHE_DIR = Path(config.DATA_DIR) / "ebird_cache"
CACHE_DIR.mkdir(exist_ok=True)

# 北京地区码（实测确认：CN-BJ 返回空，CN-11 有效）
BEIJING_REGION = "CN-11"


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


def fetch_recent_beijing(days: int = 30) -> list[dict]:
    """拉取北京最近观测（CN-11 地区码）。"""
    cache_file = CACHE_DIR / f"beijing_recent_{days}d.json"
    if cache_file.exists():
        log.info("读缓存：%s", cache_file)
        return json.loads(cache_file.read_text())

    url = f"https://api.ebird.org/v2/data/obs/{BEIJING_REGION}/recent?back={days}"
    rows = _req(url)
    if not isinstance(rows, list):
        return []

    # 按物种聚合
    by_species = defaultdict(lambda: {"count": 0, "locations": set(), "dates": set()})
    for r in rows:
        sc = r["speciesCode"]
        by_species[sc]["count"] += 1
        by_species[sc]["locations"].add(r["locName"])
        by_species[sc]["dates"].add(r["obsDt"][:10])
        by_species[sc]["comName"] = r["comName"]
        by_species[sc]["sciName"] = r["sciName"]

    out = [
        {"speciesCode": sc, "comName": v["comName"], "sciName": v["sciName"],
         "count": v["count"], "locations": list(v["locations"]),
         "dates": list(v["dates"])}
        for sc, v in sorted(by_species.items(), key=lambda x: -x[1]["count"])
    ]
    cache_file.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    log.info("缓存到 %s", cache_file)
    return out


def get_recommendations(min_conf: float = 0.45) -> list[dict]:
    """推荐清单：社区热点物种 - 我的观测 = 这个月该去看什么。

    返回 [{"species": "...", "community_count": 15, "my_count": 0,
            "locations": [...], "priority": "high"}, ...]
    """
    # 我的观测（按物种聚合，带英文名映射）
    with store.conn_ctx(readonly=True) as con:
        my_rows = con.execute(
            """SELECT s.common_name_cn, s.common_name_en, COUNT(*) c
               FROM observations o JOIN species s ON s.id = o.species_id
               WHERE o.confidence >= ? AND s.in_china = 1
               GROUP BY s.common_name_cn""", (min_conf,)).fetchall()
    # 建立英文名 -> 中文名映射
    en_to_cn = {}
    my_cn_set = set()
    my_en_map = {}
    for r in my_rows:
        my_cn_set.add(r["common_name_cn"])
        if r["common_name_en"]:
            en_to_cn[r["common_name_en"].lower()] = r["common_name_cn"]
            my_en_map[r["common_name_en"].lower()] = r["c"]

    # 社区热点（北京最近 30 天）
    community = fetch_recent_beijing(days=30)

    recommendations = []
    for sp in community:
        name_en = sp["comName"]
        name_cn = en_to_cn.get(name_en.lower(), name_en)
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
