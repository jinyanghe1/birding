"""FastAPI 网页服务端。图片通过 /img 代理，浏览器才能访问 Photos 库内的文件。"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import config, store

app = FastAPI(title="观鸟数据库", version="0.1.0")

STATIC = Path(__file__).parent / "static"

# 允许通过 /img 读取的根目录（防止任意文件读取）
_ALLOWED_ROOTS = [
    Path(config.THUMB_DIR).resolve(),
    Path(config.FRAME_DIR).resolve(),
    Path(os.path.expanduser("~/Pictures")).resolve(),
]


def _safe_path(p: str) -> Path | None:
    try:
        rp = Path(p).resolve()
    except Exception:
        return None
    for root in _ALLOWED_ROOTS:
        try:
            rp.relative_to(root)
            return rp
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ 页面
@app.get("/", response_class=HTMLResponse)
def index():
    html = STATIC / "index.html"
    if not html.exists():
        return HTMLResponse("<h1>缺少 static/index.html</h1>", status_code=500)
    return HTMLResponse(html.read_text(encoding="utf-8"))


@app.get("/img")
def img(path: str = Query(...)):
    rp = _safe_path(path)
    if rp is None or not rp.exists():
        raise HTTPException(404, "图片不可访问")
    media = "image/jpeg"
    if rp.suffix.lower() in (".png",):
        media = "image/png"
    elif rp.suffix.lower() in (".heic", ".heif"):
        media = "image/heic"
    return FileResponse(rp, media_type=media)


# ------------------------------------------------------------------ API
@app.get("/api/stats")
def api_stats(min_conf: float = 0.0):
    return store.get_stats(min_conf if min_conf > 0 else None)


@app.get("/api/species")
def api_species(order: str = "count", search: str = "", limit: int = 200,
                min_conf: float = 0.0):
    return store.get_species_list(order=order, search=search, limit=limit,
                                  min_conf=min_conf if min_conf > 0 else None)


@app.get("/api/species/{sid}")
def api_species_detail(sid: int):
    d = store.get_species_detail(sid)
    if not d:
        raise HTTPException(404, "not found")
    return d


@app.get("/api/calendar")
def api_calendar(year: int = 0, min_conf: float = 0.0):
    if not year:
        year = datetime.now(config.LOCAL_TZ).year
    return {"year": year,
            "days": store.get_calendar(year, min_conf if min_conf > 0 else None)}


@app.get("/api/places")
def api_places(limit: int = 12, min_conf: float = 0.0):
    return store.get_places(limit, min_conf if min_conf > 0 else None)


@app.get("/api/timeline")
def api_timeline(min_conf: float = 0.0):
    return store.get_timeline(min_conf if min_conf > 0 else None)


@app.get("/api/hours")
def api_hours(min_conf: float = 0.0):
    return store.get_hour_dist(min_conf if min_conf > 0 else None)


@app.get("/api/map")
def api_map(zoom: int = 3, min_conf: float = 0.0,
            sw_lat: float | None = None, sw_lon: float | None = None,
            ne_lat: float | None = None, ne_lon: float | None = None):
    """网格聚类点，供地图渲染。视口参数可选。"""
    return store.get_map_points(
        zoom=zoom, min_conf=min_conf if min_conf > 0 else None,
        sw_lat=sw_lat, sw_lon=sw_lon, ne_lat=ne_lat, ne_lon=ne_lon)


@app.get("/api/map/cell")
def api_map_cell(lat: float, lon: float, zoom: int = 12,
                 min_conf: float = 0.0):
    """点开某个簇，返回该网格内的观测明细。"""
    return store.get_observations_at(
        lat, lon, zoom=zoom,
        min_conf=min_conf if min_conf > 0 else None)


@app.post("/api/scan")
def api_scan(limit: int = 0, force: bool = False):
    """手动触发扫描。同步执行，大库可能较慢。"""
    from . import pipeline
    try:
        stats = pipeline.scan(limit=limit or None, force=force)
        return {"ok": True, "stats": stats}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/queue")
def api_queue(status: str = "pending", limit: int = 24):
    return store.get_queue(status, limit)


# ------------------------------------------------------------------ 手动导入
IMPORT_DIR = Path(config.DATA_DIR) / "imports"
IMPORT_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/upload")
async def api_upload(
    file: UploadFile = File(...),
    taken_at: str = Form(""),
    place_name: str = Form(""),
):
    """手动上传图片或视频，识别鸟种并入库。

    逻辑：新品种 -> 加新；老品种 -> 在该种类下增加图片与观测次数。
    """
    import hashlib
    import shutil
    import uuid

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".heic", ".webp", ".mp4", ".mov",
                      ".m4v", ".avi"):
        return JSONResponse({"ok": False, "error": f"不支持的格式 {suffix}"}, 400)

    raw = await file.read()
    if len(raw) > 200 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "文件过大（>200MB）"}, 400)

    uid = uuid.uuid4().hex[:12]
    fpath = IMPORT_DIR / f"{uid}{suffix}"
    fpath.write_bytes(raw)

    is_video = suffix in (".mp4", ".mov", ".m4v", ".avi")
    img_path = fpath
    if is_video:
        # 视频抽第一帧用于识别
        import subprocess
        frame = IMPORT_DIR / f"{uid}_frame.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(fpath),
                 "-frames:v", "1", "-q:v", "3", str(frame)],
                capture_output=True, timeout=60)
            if frame.exists():
                img_path = frame
        except Exception:
            pass

    # 用本地模型识别
    from . import classifier
    res = classifier.identify_file(str(img_path), topk=2)
    if not res.get("is_bird") or not res.get("candidates"):
        return JSONResponse({
            "ok": False, "error": "未识别出鸟类",
            "detail": res.get("candidates"),
        }, 200)

    top = res["candidates"][0]
    now = datetime.now(config.LOCAL_TZ)
    when = taken_at or now.strftime("%Y-%m-%dT%H:%M:%S")
    obs_date = when[:10]

    sid = store.upsert_species(top["common_name_cn"])
    place = place_name.strip() or None

    obs_id = store.find_observation(sid, obs_date, place, None, None)
    action = "merged"
    if obs_id is None:
        obs_id = store.add_observation(
            sid, obs_date, when[11:19] if len(when) > 10 else None,
            None, None, place, "manual" if place else None,
            None, top["confidence"], "manual_upload",
            f"手动上传识别（{file.filename}）",
        )
        action = "new_observation"

    from .pipeline import make_thumb
    thumb = make_thumb(str(img_path), uid)
    store.upsert_photo(
        uid, obs_id=obs_id, filename=file.filename,
        shot_at=when, media_type="video" if is_video else "image",
        image_path=str(img_path), image_source="upload",
        thumb_cache=thumb, animal_conf=res.get("box_conf"),
        is_representative=1,
    )

    sp = store.get_species_detail(sid)
    return {
        "ok": True,
        "action": action,
        "species_id": sid,
        "common_name_cn": top["common_name_cn"],
        "confidence": round(top["confidence"] * 100, 1),
        "is_new": action == "new_observation",
        "total_obs": sp["count"] if sp else 1,
        "alternatives": [c["common_name_cn"] for c in res["candidates"][1:3]],
    }
