"""eBird 同步：把你的观测记录提交到 eBird，或从 eBird 拉取你的历史记录。

鉴权：x-ebirdapitoken 请求头（免费，在 ebird.org/api/keygen 申请）。
注意：eBird API 只能操作**你自己账号**的数据，不能代他人提交。
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

from . import config, store

log = logging.getLogger("birdscan")

EBIRD_API = "https://api.ebird.org/v2"
TOKEN_FILE = Path(config.DATA_DIR) / ".ebird_token"


def _get_token() -> str | None:
    """从文件或环境变量读 token。"""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    import os
    return os.environ.get("EBIRD_API_TOKEN")


def _req(url: str, method: str = "GET", data: bytes | None = None,
         content_type: str = "application/json") -> dict | list | None:
    token = _get_token()
    if not token:
        log.error("未设置 eBird API token（存到 %s 或设环境变量 EBIRD_API_TOKEN）",
                  TOKEN_FILE)
        return None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"x-ebirdapitoken": token, "Content-Type": content_type,
                 "User-Agent": "birdscan/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log.error("eBird 请求失败 %s: %s", url, e)
        return None


# ------------------------------------------------------------------ 提交
def submit_checklist(obs_date: str, place_name: str, latitude: float | None,
                     longitude: float | None, species_counts: dict[str, int],
                     duration_min: int | None = None, notes: str = "") -> dict | None:
    """提交一份 checklist 到 eBird。

    species_counts: {"species_code": 数量}，例如 {"bcnher": 3, "litegr": 5}
    """
    url = f"{EBIRD_API}/product/checklists"
    payload = {
        "locId": _resolve_location(place_name, latitude, longitude),
        "obsDt": f"{obs_date}T00:00:00",
        "species": [{"speciesCode": k, "howMany": v}
                    for k, v in species_counts.items()],
    }
    if duration_min:
        payload["duration"] = duration_min
    if notes:
        payload["notes"] = notes
    data = json.dumps(payload).encode("utf-8")
    return _req(url, "POST", data)


def _resolve_location(place_name: str, lat: float | None,
                      lon: float | None) -> str:
    """把地名/坐标解析成 eBird 的 locId。

    eBird 需要 locId 而不是地名。如果地名在 eBird 热点里，用热点 locId；
    否则新建个人地点（用坐标）。
    """
    if lat is not None and lon is not None:
        # 查附近热点
        url = (f"{EBIRD_API}/ref/hotspot/geo?lat={lat}&lng={lon}"
               f"&dist=1&fmt=json")
        hot = _req(url)
        if hot and isinstance(hot, list) and hot:
            return hot[0].get("locId", "")
    # 兜底：新建个人地点
    url = f"{EBIRD_API}/ref/hotspot/info"
    payload = {"locName": place_name or "个人地点",
               "lat": lat or 39.9, "lng": lon or 116.4,
               "countryCode": "CN"}
    r = _req(url, "POST", json.dumps(payload).encode("utf-8"))
    return (r or {}).get("locId", "")


# ------------------------------------------------------------------ 拉取
def fetch_my_observations(region_code: str = "CN", days: int = 365) -> list[dict]:
    """拉取你最近的观测记录。

    ⚠️ 实测：这个接口返回的是**该地区所有人的观测**，不只是你的。
    要只看自己的，需要在 eBird 网页端登录后导出 CSV。
    """
    url = f"{EBIRD_API}/data/obs/{region_code}/recent?back={days}"
    r = _req(url)
    return r if isinstance(r, list) else []


def fetch_species_code(sci_name: str) -> str | None:
    """由拉丁学名查 eBird species code。"""
    url = f"{EBIRD_API}/ref/taxonomy/ebird?fmt=json"
    r = _req(url)
    if not isinstance(r, list):
        return None
    for t in r:
        if t.get("sciName", "").lower() == sci_name.lower():
            return t.get("speciesCode")
    return None


# ------------------------------------------------------------------ 导出
def export_csv(out_path: Path | None = None, min_conf: float = 0.45) -> Path:
    """导出 eBird 格式的 CSV，供手动上传到 eBird 网页端。

    eBird 网页端：https://ebird.org/import/upload.html
    模板：https://ebird.org/import/upload.html 页面上的「Download the eBird
    Record Format template」
    """
    if out_path is None:
        out_path = Path(config.DATA_DIR) / "ebird_export.csv"
    with store.conn_ctx(readonly=True) as con:
        rows = con.execute(
            """SELECT s.common_name_en, s.scientific_name, s.common_name_cn,
                      o.obs_date, o.obs_time, o.place_name, o.latitude, o.longitude,
                      o.photo_count
               FROM observations o JOIN species s ON s.id = o.species_id
               WHERE o.confidence >= ? AND s.common_name_en IS NOT NULL
               ORDER BY o.obs_date DESC""", (min_conf,)).fetchall()

    lines = ["Common Name,Count,Date,Time,Location,Latitude,Longitude,Notes"]
    for r in rows:
        loc = r["place_name"] or f"{r['latitude']},{r['longitude']}"
        notes = f"{r['common_name_cn']} | 照片 {r['photo_count']} 张"
        lines.append(f'"{r["common_name_en"]}",1,"{r["obs_date"]}",'
                     f'"{r["obs_time"] or "12:00"}","{loc}",'
                     f'{r["latitude"] or ""},{r["longitude"] or ""},'
                     f'"{notes}"')
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("导出 eBird CSV：%s（%d 条）", out_path, len(rows))
    return out_path
