"""FastAPI 网页服务端。图片通过 /img 代理，浏览器才能访问 Photos 库内的文件。"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, store

log = logging.getLogger("birdscan.web")

app = FastAPI(title="观鸟数据库", version="0.2.0")


@app.middleware("http")
async def prevent_stale_frontend(request, call_next):
    """HTML/JS/CSS 必须 revalidate，避免部署后旧浏览器继续运行过期资源。"""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path == "/sw.js" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


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


@app.get("/manifest.json")
def manifest():
    f = STATIC / "manifest.json"
    if f.exists():
        return FileResponse(f, media_type="application/json")
    raise HTTPException(status_code=404)


@app.get("/sw.js")
def service_worker():
    f = STATIC / "sw.js"
    if f.exists():
        return FileResponse(f, media_type="application/javascript")
    raise HTTPException(status_code=404)


# 静态资源（前端模块化后的 css/js）
app.mount("/static", StaticFiles(directory=STATIC), name="static")
# 缩略图直出（本地开发无 Nginx 时也能显示；服务器上 Nginx 优先）
app.mount("/thumbs", StaticFiles(directory=config.THUMB_DIR), name="thumbs")


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


@app.get("/full/{filename}")
def full_image(filename: str):
    """独立全图路由；只接受导出生成的安全文件名，不能读取任意路径。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}\.(?:jpg|jpeg|png|webp)", filename):
        raise HTTPException(404, "图片不可访问")
    root = Path(config.FULL_DIR).resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(404, "图片不可访问")
    if not path.is_file():
        raise HTTPException(404, "全图尚未同步")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ API（读：匿名开放）
@app.get("/api/stats")
def api_stats(min_conf: float = 0.0):
    return store.get_stats(min_conf if min_conf > 0 else None)


@app.get("/api/config/baidu-map-ak")
def api_baidu_map_ak():
    """百度地图服务端 AK（从 secrets 文件读，不进 git）。"""
    from pathlib import Path
    ak_file = Path(config.DATA_DIR) / "secrets" / "baidu_map_ak.txt"
    if ak_file.exists():
        return {"ak": ak_file.read_text().strip()}
    return {"ak": ""}


@app.get("/api/species/search")
def api_species_search(q: str = "", limit: int = 10):
    """物种自动补全：正名/别名/英文名/拉丁名模糊匹配，返回规范名。"""
    return store.search_species(q, limit)


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


@app.get("/api/config/home")
def api_get_home():
    """常住地（地图默认中心 + 推荐城市），含可选城市列表。"""
    from . import locations
    return {**locations.get_home(), "cities": locations.list_cities()}


@app.post("/api/config/home")
def api_set_home(city: str, _: None = Depends(auth.verify_api_key)):
    """设置常住地（写操作需鉴权）。"""
    from . import locations
    out = locations.set_home(city)
    if not out.get("ok"):
        raise HTTPException(400, out.get("reason", "bad_city"))
    return out


@app.get("/api/recommendations")
def api_recommendations(city: str | None = None, region: str | None = None,
                        lat: float | None = None, lon: float | None = None,
                        days: int = 30):
    """推荐清单：当地社区热点物种 - 我的观测 = 这个月该去看什么。

    优先级：lat/lon（浏览器定位）> city（中文城市名）> region（eBird 区域码）> 常住地。
    """
    from . import ebird_calendar, locations
    if lat is not None and lon is not None:
        return ebird_calendar.get_recommendations(lat=lat, lon=lon, days=days)
    if city:
        coords = locations.coords_for(city)
        if not coords:
            raise HTTPException(400, "unknown_city")
        region = coords[2]
    if not region:
        region = locations.get_home()["region"]
    return ebird_calendar.get_recommendations(region=region, days=days)


@app.get("/api/migration")
def api_migration(species_id: int | None = None, min_conf: float = 0.0):
    """按物种聚合历年观测的月份分布，用于迁徙日历。"""
    return store.get_species_monthly_pattern(
        species_id, min_conf if min_conf > 0 else None)


@app.get("/api/first-seen")
def api_first_seen(min_conf: float = 0.0):
    """每个物种的首次观测地点，用于「加新地图」。"""
    return store.get_first_seen_map(min_conf if min_conf > 0 else None)


@app.get("/api/map")
def api_map(zoom: int = 3, min_conf: float = 0.0,
            sw_lat: float | None = None, sw_lon: float | None = None,
            ne_lat: float | None = None, ne_lon: float | None = None):
    """网格聚类点，供地图渲染。视口参数可选。"""
    return store.get_map_points(
        zoom=zoom, min_conf=min_conf if min_conf > 0 else None,
        sw_lat=sw_lat, sw_lon=sw_lon, ne_lat=ne_lat, ne_lon=ne_lon)


@app.get("/api/map/cell")
def api_map_cell(lat: float, lon: float, zoom: int = 12, min_conf: float = 0.0):
    """点开某个簇，返回该网格内的观测明细。"""
    return store.get_observations_at(
        lat, lon, zoom=zoom, min_conf=min_conf if min_conf > 0 else None)


@app.post("/api/scan")
def api_scan(limit: int = 0, force: bool = False,
             _: None = Depends(auth.verify_api_key)):
    """手动触发扫描。同步执行，大库可能较慢。需鉴权（P0-4 修复）。"""
    from . import pipeline
    try:
        stats = pipeline.scan(limit=limit or None, force=force)
        return {"ok": True, "stats": stats}
    except Exception:
        log.exception("扫描失败")
        return JSONResponse(
            {"ok": False, "error": "扫描失败，请查看服务器日志"}, status_code=500)


@app.get("/api/queue")
def api_queue(status: str = "pending", limit: int = 24):
    return store.get_queue(status, limit)


# ------------------------------------------------------------------ CRUD 删除
@app.delete("/api/photo/{asset_uuid}")
def api_delete_photo(asset_uuid: str,
                     _: None = Depends(auth.verify_api_key)):
    """删除一张照片（误识别清理）。"""
    return store.delete_photo(asset_uuid)


@app.delete("/api/observation/{obs_id}")
def api_delete_observation(obs_id: int,
                           _: None = Depends(auth.verify_api_key)):
    """删除一条观测记录及其所有照片。"""
    return store.delete_observation(obs_id)


# ------------------------------------------------------------------ 复核工作流
@app.get("/api/review/suspect")
def api_suspect(limit: int = 100):
    """取存疑样本（低置信度 或 不在中国名录）。"""
    return store.get_suspect_samples(limit)


@app.post("/api/review/not-bird")
def api_mark_not_bird(obs_id: int,
                      _: None = Depends(auth.verify_api_key)):
    """标记「不是鸟」：删除该观测及其照片。"""
    return store.mark_not_bird(obs_id)


@app.post("/api/review/reassign")
def api_reassign(obs_id: int, new_species_cn: str = "",
                 species_id: int | None = None,
                 _: None = Depends(auth.verify_api_key)):
    """标记「分类错误」：把观测改到另一个物种下。

    优先 species_id（自动补全选中的规范物种）；否则按名字解析别名后归一。
    """
    out = store.reassign_species(obs_id, new_species_cn or None, species_id)
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


@app.get("/api/species/{sid}/aliases")
def api_species_aliases(sid: int):
    """某物种的别名列表（用于展示同鸟多名归一结果）。"""
    return {"species_id": sid, "aliases": store.get_aliases(sid)}


# ------------------------------------------------------------------ 手动导入
IMPORT_DIR = Path(config.DATA_DIR) / "imports"
IMPORT_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/upload")
async def api_upload(
    file: UploadFile = File(...),
    taken_at: str = Form(""),
    place_name: str = Form(""),
    _: None = Depends(auth.verify_api_key),
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

    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
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

    # 用本地模型识别（TTA 投票降低方差）
    from . import classifier
    res = classifier.identify_file_tta(str(img_path), topk=2)
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


@app.on_event("startup")
def _init_db():
    store.init_db()
