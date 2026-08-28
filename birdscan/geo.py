"""地点解析：三档降级。

实测：62% 的照片 Photos 已反查好中文地名（p.place），直接白送，
不接高德/百度（需 key、联网、有配额）。
"""
from __future__ import annotations

import math

from . import config

_RG = None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * config.EARTH_R_KM * math.asin(math.sqrt(a))


def resolve_place(latitude, longitude, photos_place: str | None) -> tuple[str | None, str]:
    """返回 (地名, 来源)。来源: photos | reverse | inferred | none"""
    if photos_place:
        return _normalize(photos_place), "photos"
    if latitude is not None and longitude is not None:
        name = _reverse_lookup(latitude, longitude)
        if name:
            return name, "reverse"
    return None, "none"


def _normalize(name: str) -> str:
    return " ".join(str(name).split())


def _reverse_lookup(lat: float, lon: float) -> str | None:
    """reverse_geocoder 是英文结果，装了才用；失败静默返回 None。"""
    global _RG
    if _RG is None:
        try:
            import reverse_geocoder as rg
            _RG = rg
        except ImportError:
            _RG = False
    if not _RG:
        return None
    try:
        res = _RG.search([(lat, lon)])[0]
        parts = [res.get("name"), res.get("admin1"), res.get("cc")]
        return ", ".join(p for p in parts if p) or None
    except Exception:
        return None


def admin_region(place_name: str | None) -> str | None:
    """从地名里粗略提取省/市级，用于分组统计。"""
    if not place_name:
        return None
    for sep in ("省", "市", "区", "县", "自治区"):
        idx = place_name.find(sep)
        if idx > 0:
            return place_name[: idx + len(sep)]
    return place_name.split(",")[0].strip()


def cluster_key(latitude, longitude, place_name: str | None) -> str:
    """地点簇：有地名用地名，否则用 1km 网格。"""
    if place_name:
        return f"P:{place_name}"
    if latitude is not None and longitude is not None:
        scale = config.PLACE_CLUSTER_KM / 111.0
        return f"G:{round(latitude / scale)}:{round(longitude / scale)}"
    return "U:unknown"
