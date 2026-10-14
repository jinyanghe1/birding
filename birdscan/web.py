"""FastAPI 网页服务端。图片通过 /img 代理，浏览器才能访问 Photos 库内的文件。"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import auth, config, store

log = logging.getLogger("birdscan.web")

app = FastAPI(title="观鸟数据库", version="0.2.0")

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


# ------------------------------------------------------------------ API
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


@app.get("/api/recommendations")
def api_recommendations():
    """推荐清单：社区热点物种 - 我的观测 = 这个月该去看什么。"""
    from . import ebird_calendar
    return ebird_calendar.get_recommendations()


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
def api_map_cell(lat: float, lon: float, zoom: int = 12,
                 min_conf: float = 0.0):
    """点开某个簇，返回该网格内的观测明细。"""
    return store.get_observations_at(
        lat, lon, zoom=zoom,
        min_conf=min_conf if min_conf > 0 else None)


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
def api_delete_photo(asset_uuid: str, _: None = Depends(auth.verify_api_key)):
    """删除一张照片（误识别清理）。"""
    return store.delete_photo(asset_uuid)


@app.delete("/api/observation/{obs_id}")
def api_delete_observation(obs_id: int, _: None = Depends(auth.verify_api_key)):
    """删除一条观测记录及其所有照片。"""
    return store.delete_observation(obs_id)


# ------------------------------------------------------------------ 复核工作流
@app.get("/api/review/suspect")
def api_suspect(limit: int = 100):
    """取存疑样本（低置信度 或 不在中国名录）。"""
    return store.get_suspect_samples(limit)


@app.post("/api/review/not-bird")
def api_mark_not_bird(obs_id: int, _: None = Depends(auth.verify_api_key)):
    """标记「不是鸟」：删除该观测及其照片。"""
    return store.mark_not_bird(obs_id)


@app.post("/api/review/reassign")
def api_reassign(obs_id: int, new_species_cn: str, _: None = Depends(auth.verify_api_key)):
    """标记「分类错误」：把观测改到另一个物种下。"""
    return store.reassign_species(obs_id, new_species_cn)


# ------------------------------------------------------------------ 手动导入
IMPORT_DIR = Path(config.DATA_DIR) / "imports"
IMPORT_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/upload")
async def api_upload(
    _: None = Depends(auth.verify_api_key),
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


# ------------------------------------------------------------------ 家庭共享相册
from . import sharing_v2 as sharing

# 相册上传安全限制（P1-8 大小 / P1-11 类型白名单）
ALBUM_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic",
                      ".mp4", ".mov", ".m4v", ".avi"}
ALBUM_MAX_BYTES = 50 * 1024 * 1024          # 50MB（/api/upload 为 200MB）
# 下载侧安全媒体类型（白名单外一律 application/octet-stream，防存储型 XSS）
_SAFE_MEDIA = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heic",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4",
    ".avi": "video/x-msvideo",
}
_NOSNIFF = {"X-Content-Type-Options": "nosniff"}


def _current_user(request: Request) -> dict:
    """从 X-API-Key 头或 Cookie 解析当前用户。

    Cookie 用于 <img src>/window.open 等无法带自定义 header 的场景。
    """
    key = auth.key_from_request(request)
    user = sharing.get_user_by_key(key)
    if not user:
        raise HTTPException(status_code=401, detail="未授权")
    return user


@app.post("/api/auth/session")
def api_set_session(request: Request):
    """用 API Key 换登录态 Cookie（30 天，HttpOnly + SameSite=Lax）。"""
    key = auth.key_from_request(request)
    user = sharing.get_user_by_key(key)
    if not user:
        raise HTTPException(status_code=401, detail="API Key 无效")
    resp = JSONResponse({"ok": True,
                         "user": {k: user[k] for k in
                                  ("id", "username", "display_name", "role")}})
    resp.set_cookie(auth.COOKIE_NAME, key, max_age=30 * 24 * 3600,
                    httponly=True, samesite="lax", path="/")
    return resp


@app.post("/api/auth/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


@app.on_event("startup")
def _init_sharing():
    sharing.init_sharing()


@app.post("/api/auth/login")
def api_login(body: dict):
    user = sharing.get_user_by_key(body.get("api_key", ""))
    if not user:
        raise HTTPException(status_code=401, detail="API Key 无效")
    return {"user": {k: user[k] for k in ("id", "username", "display_name", "role")}}


@app.post("/api/invites")
def api_create_invite(request: Request, body: dict):
    key = auth.key_from_request(request)
    inv = sharing.create_invite(key, body.get("album_ids", []), body.get("max_uses", 1))
    if not inv:
        raise HTTPException(status_code=403, detail="只有 owner 能生成邀请码")
    return inv


@app.get("/api/invites/{code}")
def api_check_invite(code: str):
    """验证邀请码有效性（不消耗）。"""
    with store.conn_ctx(readonly=True) as con:
        inv = con.execute(
            "SELECT * FROM invites WHERE code=? AND used_count < max_uses",
            (code,)).fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail="邀请码无效或已用完")
        return {"valid": True, "album_ids": json.loads(inv["album_ids"])}


@app.post("/api/invites/{code}/use")
def api_use_invite(code: str, body: dict):
    r = sharing.use_invite(code, body.get("display_name", ""))
    if not r:
        raise HTTPException(status_code=400, detail="邀请码无效或已用完")
    return r


@app.get("/api/albums")
def api_list_albums(request: Request):
    return sharing.list_albums(_current_user(request))


@app.post("/api/albums")
def api_create_album(request: Request, body: dict):
    try:
        return sharing.create_album(_current_user(request),
                                    body.get("name", ""), body.get("description", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/albums/{album_id}/photos")
def api_album_photos(album_id: int, request: Request,
                     limit: int = 200, offset: int = 0):
    rows = sharing.list_album_photos(album_id, _current_user(request), limit, offset)
    if rows is None:
        raise HTTPException(status_code=404, detail="相册不存在或无权限")
    return rows


@app.post("/api/albums/{album_id}/photos")
async def api_upload_to_album(album_id: int, request: Request,
                              files: list[UploadFile] = File(...)):
    user = _current_user(request)
    if not sharing.get_album(album_id, user):
        raise HTTPException(status_code=404, detail="相册不存在或无权限")
    results = []
    for f in files:
        try:
            results.append(await _save_cloud_photo(user, album_id, f))
        except Exception as e:
            results.append({"ok": False, "name": f.filename, "error": str(e)})
    return results


async def _save_cloud_photo(user: dict, album_id: int, f: UploadFile) -> dict:
    """保存上传：原图 + 缩略图 + EXIF + 哈希去重。"""
    import uuid
    import hashlib
    import io
    import json as _json
    from PIL import Image
    from PIL.ExifTags import TAGS

    # 类型白名单（P1-11：防 .html/.svg 存储型 XSS）
    ext = Path(f.filename or "").suffix.lower()
    if ext not in ALBUM_ALLOWED_EXTS:
        raise HTTPException(400, f"不支持的格式 {ext or '(无扩展名)'}")

    raw = await f.read()
    # 大小限制（P1-8：防内存耗尽 DoS）
    if len(raw) > ALBUM_MAX_BYTES:
        raise HTTPException(
            413, f"文件过大（>{ALBUM_MAX_BYTES // 1024 // 1024}MB）")

    # 计算 SHA256 用于去重
    file_hash = hashlib.sha256(raw).hexdigest()

    # 检查是否已存在
    existing = sharing.get_photo_by_hash(file_hash)
    if existing:
        # 已存在：只加关联，不重复存储
        sharing.add_photo_to_album(existing["id"], album_id, user)
        return {"ok": True, "id": existing["id"], "name": f.filename, "duplicate": True}

    # 不存在：存原图 + 缩略图
    fname = f"{uuid.uuid4().hex}{ext}"
    orig_rel = f"orig/{fname}"
    thumb_rel = f"thumbs/{fname}"
    orig_fp = sharing.CLOUD_ORIG / fname
    thumb_fp = sharing.CLOUD_THUMBS / fname
    orig_fp.write_bytes(raw)

    width = height = None
    shot_at = None
    exif = {}
    is_video = ext in (".mp4", ".mov", ".m4v", ".avi")
    media_type = "video" if is_video else "image"
    if is_video:
        # 视频抽第一帧做缩略图；失败则占位图
        try:
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(orig_fp),
                 "-frames:v", "1", "-q:v", "3", str(thumb_fp)],
                capture_output=True, timeout=60)
        except Exception:
            pass
        if not thumb_fp.exists() or thumb_fp.stat().st_size == 0:
            _placeholder_thumb(thumb_fp)
    else:
        try:
            im = Image.open(io.BytesIO(raw))
            width, height = im.size
            ex = im.getexif()
            exif = {TAGS.get(k, k): str(v) for k, v in ex.items()}
            dt = exif.get("DateTimeOriginal") or exif.get("DateTime")
            if dt:
                shot_at = dt.replace(":", "-", 2)
            # 缩略图 512px
            im.thumbnail((512, 512))
            im.convert("RGB").save(thumb_fp, "JPEG", quality=82)
        except Exception:
            _placeholder_thumb(thumb_fp)

    try:
        rec = sharing.add_cloud_photo(
            user, album_id, file_hash, filename=fname, orig_name=f.filename,
            size_bytes=len(raw), width=width, height=height, shot_at=shot_at,
            media_type=media_type, exif_json=_json.dumps(exif, ensure_ascii=False),
            thumb_path=thumb_rel, orig_path=orig_rel)
    except Exception:
        # DB 失败：清理已落盘文件，避免孤儿（P1-9）
        for fp in (orig_fp, thumb_fp):
            try:
                if fp.exists():
                    fp.unlink()
            except OSError:
                pass
        raise
    return {"ok": True, "id": rec["id"], "name": f.filename, "duplicate": False}


def _placeholder_thumb(fp: Path) -> None:
    """生成占位缩略图（视频无帧/图片解析失败时）。"""
    try:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (320, 240), (220, 220, 224))
        d = ImageDraw.Draw(im)
        d.text((12, 12), "VIDEO", fill=(120, 120, 128))
        im.save(fp, "JPEG", quality=70)
    except Exception:
        fp.write_bytes(b"")


@app.get("/api/photos/{photo_id}")
def api_cloud_photo(photo_id: int, request: Request):
    p = sharing.get_cloud_photo(photo_id, _current_user(request))
    if not p:
        raise HTTPException(status_code=404, detail="照片不存在")
    fp = sharing.CLOUD_DIR / p["orig_path"]
    if not fp.exists():
        raise HTTPException(status_code=404, detail="文件缺失")
    media = _SAFE_MEDIA.get(Path(p["orig_path"]).suffix.lower(),
                            "application/octet-stream")
    return FileResponse(fp, media_type=media, headers=_NOSNIFF)


@app.get("/api/photos/{photo_id}/thumb")
def api_cloud_photo_thumb(photo_id: int, request: Request):
    p = sharing.get_cloud_photo(photo_id, _current_user(request))
    if not p:
        raise HTTPException(status_code=404, detail="照片不存在")
    fp = sharing.CLOUD_DIR / p["thumb_path"]
    if not fp.exists() or fp.stat().st_size == 0:
        fp = sharing.CLOUD_DIR / p["orig_path"]
    media = _SAFE_MEDIA.get(Path(p["orig_path"]).suffix.lower(),
                            "application/octet-stream")
    if fp.suffix.lower() not in _SAFE_MEDIA:
        media = "image/jpeg"  # 缩略图恒为 JPEG（含视频抽帧）
    return FileResponse(fp, media_type=media, headers=_NOSNIFF)


@app.delete("/api/photos/{photo_id}")
def api_delete_cloud_photo(photo_id: int, request: Request):
    if not sharing.delete_cloud_photo(photo_id, _current_user(request)):
        raise HTTPException(status_code=404, detail="照片不存在或无权限")
    return {"ok": True}


@app.get("/api/albums/{album_id}/download")
def api_download_album(album_id: int, request: Request):
    """整本相册打包 zip（流式）。"""
    import zipfile, tempfile
    user = _current_user(request)
    rows = sharing.list_album_photos(album_id, user, limit=10000)
    if rows is None:
        raise HTTPException(status_code=404, detail="相册不存在或无权限")
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
        for p in rows:
            fp = sharing.CLOUD_DIR / p["orig_path"]
            if fp.exists():
                z.write(fp, arcname=p["orig_name"] or p["filename"])
    tmp.close()
    return FileResponse(
        tmp.name, filename=f"album-{album_id}.zip",
        background=BackgroundTask(_safe_unlink, tmp.name))


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@app.delete("/api/albums/{album_id}")
def api_delete_album(album_id: int, request: Request):
    """删除相册（需鉴权，owner 或相册创建者；级联 GC 无引用照片）。"""
    user = _current_user(request)
    r = sharing.delete_album(album_id, user)
    if not r:
        if not sharing.get_album(album_id, user):
            raise HTTPException(status_code=404, detail="相册不存在或无权限")
        raise HTTPException(status_code=403, detail="只有相册创建者能删除")
    return r


@app.post("/api/sync/pull")
def api_sync_pull(request: Request, body: dict):
    rows = sharing.sync_pull_manifest(_current_user(request), body.get("album_id", 0))
    if rows is None:
        raise HTTPException(status_code=404, detail="相册不存在或无权限")
    return rows


@app.post("/api/sync/push")
def api_sync_push(request: Request, body: dict):
    """标记照片同步状态（仅上传者本人或 owner）。"""
    user = _current_user(request)
    marks = body.get("marks", {})
    if not isinstance(marks, dict):
        raise HTTPException(status_code=400, detail="marks 必须是对象")
    ok, failed = 0, []
    for pid, status in marks.items():
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            failed.append(pid)
            continue
        if sharing.sync_mark(pid_i, status, user):
            ok += 1
        else:
            failed.append(pid)
    return {"ok": True, "marked": ok, "failed": failed}
