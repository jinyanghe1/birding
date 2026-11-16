"""常住地 / 城市坐标与 eBird 区域映射。

用途：
- 地图默认中心（不再硬编码中国视图或北京）
- 推荐页按城市拉当地常见鸟种（eBird region code）

城市可自由扩展；settings 存 data/settings.json，key = home_city。
"""
from __future__ import annotations

import json
import logging

from . import config

log = logging.getLogger("birdscan")

DEFAULT_CITY = "深圳"

# city -> (lat, lon, eBird region code)
CITIES: dict[str, tuple[float, float, str]] = {
    "深圳": (22.5431, 114.0579, "CN-44"),
    "广州": (23.1291, 113.2644, "CN-44"),
    "珠海": (22.2710, 113.5767, "CN-44"),
    "北京": (39.9042, 116.4074, "CN-11"),
    "上海": (31.2304, 121.4737, "CN-31"),
    "天津": (39.0842, 117.2009, "CN-12"),
    "杭州": (30.2741, 120.1551, "CN-33"),
    "南京": (32.0603, 118.7969, "CN-32"),
    "苏州": (31.2989, 120.5853, "CN-32"),
    "成都": (30.5728, 104.0668, "CN-51"),
    "重庆": (29.5630, 106.5516, "CN-50"),
    "武汉": (30.5928, 114.3055, "CN-42"),
    "西安": (34.3416, 108.9398, "CN-61"),
    "厦门": (24.4798, 118.0894, "CN-35"),
    "福州": (26.0745, 119.2965, "CN-35"),
    "昆明": (25.0389, 102.7183, "CN-53"),
    "长沙": (28.2282, 112.9388, "CN-43"),
    "青岛": (36.0671, 120.3826, "CN-37"),
    "大连": (38.9140, 121.6147, "CN-21"),
    "中国香港": (22.3193, 114.1694, "HK"),
}


def _settings_path():
    return config.DATA_DIR / "settings.json"


def list_cities() -> list[str]:
    return list(CITIES)


def normalize_city(name: str | None) -> str | None:
    """把「深圳市」「广东深圳」等写法归一到 CITIES 里的键。"""
    if not name:
        return None
    name = name.strip()
    for city in CITIES:
        if city in name:
            return city
    return None


def get_home() -> dict:
    """常住地：settings 优先 → 观测最多的城市 → 默认深圳。"""
    city = None
    source = "default"
    try:
        p = _settings_path()
        if p.exists():
            city = normalize_city(json.loads(p.read_text()).get("home_city"))
            if city:
                source = "settings"
    except Exception as e:
        log.debug("读 settings 失败: %s", e)
    if not city:
        try:
            from . import store
            home = store.get_home_region()
            if home:
                city = normalize_city(home.get("city")) or normalize_city(home.get("region"))
                if city:
                    source = "observations"
        except Exception as e:
            log.debug("推断常住地失败: %s", e)
    city = city or DEFAULT_CITY
    lat, lon, region = CITIES[city]
    return {"city": city, "lat": lat, "lon": lon, "region": region,
            "source": source}


def set_home(city: str) -> dict:
    """设置常住地（写入 data/settings.json）。未知城市报错，不写文件。"""
    norm = normalize_city(city)
    if not norm:
        return {"ok": False, "reason": "unknown_city", "cities": list_cities()}
    p = _settings_path()
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except Exception:
            data = {}
    data["home_city"] = norm
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return {"ok": True, **get_home()}


def coords_for(city: str | None) -> tuple[float, float, str] | None:
    norm = normalize_city(city)
    return CITIES.get(norm) if norm else None
