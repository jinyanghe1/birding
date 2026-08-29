"""把数据库烘成静态 JSON + 缩略图，用于免费静态托管（GitHub Pages 等）。

产物目录 data/export_site/：
  api/stats.json
  api/species.json
  api/species/<id>.json
  api/calendar-2026.json ...
  api/places.json  api/timeline.json  api/hours.json
  api/map-z3.json ... api/map-z9.json
  thumbs/<uuid>.jpg

用法：python -m birdscan.cli export-site [--no-thumbs] [--min-conf 0.45]
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from . import config, store

log = logging.getLogger("birdscan")

OUT = Path(config.DATA_DIR) / "export_site"

def export(min_conf: float | None = None, with_thumbs: bool = True) -> dict:
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    store.init_db()
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "api").mkdir(parents=True, exist_ok=True)
    (OUT / "api" / "species").mkdir(parents=True, exist_ok=True)
    (OUT / "thumbs").mkdir(parents=True, exist_ok=True)

    # 拷贝前端并改为静态模式
    src_html = Path(config.ROOT) / "birdscan" / "static" / "index.html"
    if src_html.exists():
        html = src_html.read_text(encoding="utf-8")
        html = html.replace(
            "const STATIC_MODE = (location.hostname.includes('github.io') "
            "|| location.protocol === 'file:');",
            "const STATIC_MODE = true;  // 静态导出（GitHub Pages）"
        )
        (OUT / "index.html").write_text(html, encoding="utf-8")

    def w(rel, obj):
        (OUT / "api" / rel).write_text(
            json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")

    # 缩略图映射：绝对路径 -> 相对
    id_map = _thumb_map(with_thumbs)
    w("stats.json", store.get_stats(min_conf=mc))
    species = store.get_species_list(min_conf=mc, limit=5000)
    for s in species:
        s["thumb"] = id_map.get(s.get("thumb"), s.get("thumb"))
        s["img"] = id_map.get(s.get("img"), s.get("img"))
    w("species.json", species)

    n_detail = 0
    for s in species:
        d = store.get_species_detail(s["id"])
        if not d:
            continue
        for o in d["observations"]:
            for p in o.get("photos", []):
                for k in ("thumb_cache", "image_path"):
                    p[k] = id_map.get(p.get(k), p.get(k))
        w(f"species/{s['id']}.json", d)
        n_detail += 1

    import datetime as dt
    years = sorted({str(o)[:4] for o in store.get_timeline(min_conf=mc)} | {str(dt.date.today().year)})
    for y in {int(y) for y in years if y.isdigit()}:
        w(f"calendar-{y}.json", {"year": y, "days": store.get_calendar(y, mc)})
    w("places.json", store.get_places(12, mc))
    w("timeline.json", store.get_timeline(mc))
    w("hours.json", store.get_hour_dist(mc))

    for z in range(2, 10):
        pts = store.get_map_points(zoom=z, min_conf=mc)
        for p in pts:
            p["thumb"] = id_map.get(p.get("thumb"), p.get("thumb"))
        w(f"map-z{z}.json", pts)

    # 单元格详情
    n_cell = 0
    seen = set()
    for z in range(2, 10):
        for p in store.get_map_points(zoom=z, min_conf=mc):
            key = (round(p["lat"], 4), round(p["lon"], 4))
            if key in seen:
                continue
            seen.add(key)
            cell = store.get_observations_at(p["lat"], p["lon"], zoom=z, min_conf=mc)
            for c in cell:
                c["thumb"] = id_map.get(c.get("thumb"), c.get("thumb"))
                c["img"] = id_map.get(c.get("img"), c.get("img"))
            w(f"cell-{key[0]}-{key[1]}.json", cell)
            n_cell += 1

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1e6
    log.info("导出完成：%s（%.1f MB，%d 物种详情，%d 地图单元格）",
             OUT, size, n_detail, n_cell)
    return {"dir": str(OUT), "mb": round(size, 1),
            "species": len(species), "details": n_detail, "cells": n_cell,
            "thumbs": len(id_map)}


def _thumb_map(with_thumbs: bool) -> dict:
    if not with_thumbs:
        return {}
    m = {}
    src = Path(config.THUMB_DIR)
    if not src.exists():
        return m
    for f in src.glob("*.jpg"):
        dst = f"thumbs/{f.name}"
        shutil.copy2(f, OUT / "thumbs" / f.name)
        m[str(f)] = dst
    log.info("拷贝缩略图 %d 张", len(m))
    return m
