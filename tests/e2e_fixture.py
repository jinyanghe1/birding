"""GitHub Actions 的独立 E2E 数据库与服务。

只接受工作区以外的临时目录，避免误写开发机真实 data/birds.db。
所有外部推荐数据来自 fixture 缓存，浏览器测试不依赖 eBird 实时网络。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

# 直接执行 tests/e2e_fixture.py 时，sys.path[0] 是 tests/，需显式加入项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from birdscan import config


def configure(data_dir: str) -> Path:
    data = Path(data_dir).expanduser().resolve()
    project = config.ROOT.resolve()
    real_data = config.DATA_DIR.resolve()
    if data == real_data or data == project or project in data.parents:
        raise SystemExit("E2E 数据目录必须位于项目工作区之外，请使用 runner 的临时目录")
    for sub in ("thumbs", "full", "frames", "cache"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    config.DATA_DIR = data
    config.DB_PATH = data / "birds.db"
    config.THUMB_DIR = data / "thumbs"
    config.FULL_DIR = data / "full"
    config.FRAME_DIR = data / "frames"
    return data


def _jpeg(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)


def seed(data: Path) -> None:
    from birdscan import store

    store.init_db()
    white = store.upsert_species("白鹭")
    store.update_species(white, {
        "scientific_name": "Egretta garzetta", "common_name_en": "Little Egret",
        "family_cn": "鹭科", "in_china": 1,
    })
    # 第一鸟种包含两张 >320px 原图，完整覆盖灯箱加载与前后导航。
    for i, day in enumerate(("2026-11-15", "2026-11-14"), start=1):
        obs = store.add_observation(
            white, day, "08:00", 22.5431, 114.0579, "深圳市, 广东省, 中国",
            "test", "CN-44", 0.95, "fixture", None,
        )
        uuid = f"E2E-WHITE-{i}"
        thumb = config.THUMB_DIR / f"{uuid}.jpg"
        full = config.FULL_DIR / f"{uuid}.jpg"
        _jpeg(thumb, (320, 240), (220 - i * 20, 230, 240))
        _jpeg(full, (1600, 1200), (220 - i * 20, 230, 240))
        store.upsert_photo(
            uuid, obs_id=obs, media_type="image", image_source="original",
            image_path=str(thumb), thumb_cache=str(thumb), full_path=str(full),
            full_kind="original", full_width=1600, full_height=1200,
            width=1600, height=1200, sharpness=1000 - i,
            is_representative=int(i == 1),
        )

    # 两条低置信记录供双确认删除与别名修正交互使用；写请求在浏览器层拦截。
    for name, confidence, uuid in (("麻雀", 0.10, "E2E-REVIEW-1"),
                                   ("玄凤鹦鹉", 0.20, "E2E-REVIEW-2")):
        sid = store.upsert_species(name)
        store.update_species(sid, {"in_china": 1})
        obs = store.add_observation(
            sid, "2026-11-13", "09:00", 22.5431, 114.0579,
            "深圳市, 广东省, 中国", "test", "CN-44", confidence, "fixture", None,
        )
        thumb = config.THUMB_DIR / f"{uuid}.jpg"
        _jpeg(thumb, (320, 240), (180, 170, 150))
        store.upsert_photo(
            uuid, obs_id=obs, media_type="image", image_source="derivative",
            image_path=str(thumb), thumb_cache=str(thumb), width=320, height=240,
        )
        if name == "玄凤鹦鹉":
            store.add_alias(sid, "玄凤", "fixture")
            store.add_alias(sid, "鸡尾鹦鹉", "fixture")

    # ebird_calendar 的缓存格式：最近观测已按物种聚合；中文名缓存保留原始行。
    community = [
        {"speciesCode": "chiegr", "comName": "Chinese Egret",
         "sciName": "Egretta eulophotes", "count": 8,
         "locations": ["深圳湾公园"], "dates": ["2026-11-15"]},
        {"speciesCode": "bkcnigh", "comName": "Black-crowned Night-Heron",
         "sciName": "Nycticorax nycticorax", "count": 5,
         "locations": ["莲花山公园"], "dates": ["2026-11-14"]},
    ]
    for key in ("region_CN-44", "region_CN-11", "nearby_22.5431_114.0579"):
        (data / "cache" / f"recent_{key}_30d.json").write_text(
            json.dumps(community, ensure_ascii=False), encoding="utf-8")
    localized = [
        {"comName": "黄嘴白鹭", "comNameEn": "Chinese Egret",
         "sciName": "Egretta eulophotes"},
        {"comName": "夜鹭", "comNameEn": "Black-crowned Night-Heron",
         "sciName": "Nycticorax nycticorax"},
    ]
    (data / "cache" / "ebird_taxonomy_zh_SIM.json").write_text(
        json.dumps(localized, ensure_ascii=False), encoding="utf-8")


def serve(data: Path, port: int) -> None:
    # 必须在 web/auth/ebird_calendar 导入前切换路径，确保所有模块使用同一临时库。
    import uvicorn
    from birdscan.web import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    data = configure(args.data_dir)
    if not config.DB_PATH.exists():
        seed(data)
    if not args.seed_only:
        serve(data, args.port)


if __name__ == "__main__":
    main()
