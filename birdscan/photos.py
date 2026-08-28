"""L0 读取层：用 osxphotos 读 macOS 照片库。

关键实测约束（本机 macOS 27.0 / 15,413 张资产）：
  * 只有 11% 的照片本地有原图，其余在 iCloud；
  * 但 100% 的照片都有本地缩略图 `path_derivatives`；
  * 因此一律用缩略图识别，绝不调用 export(use_photos_export=True)。
  * `path_derivatives` 是属性不是方法。
  * 判 GPS 要用 latitude is not None（p.location 可能是 truthy 的 (None, None)）。
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import config

log = logging.getLogger("birdscan")

_DB = None


def open_library():
    """进程内只打开一次，加载约 3 秒。"""
    global _DB
    if _DB is None:
        import osxphotos
        logging.getLogger("osxphotos").setLevel(logging.ERROR)
        _DB = osxphotos.PhotosDB(config.LIBRARY_PATH) if config.LIBRARY_PATH \
            else osxphotos.PhotosDB()
    return _DB


@dataclass
class Asset:
    uuid: str
    filename: str
    date: datetime | None
    date_added: datetime | None
    latitude: float | None
    longitude: float | None
    place_name: str | None
    width: int
    height: int
    ismovie: bool
    uti: str
    image_source: str | None = None      # 'derivative' | 'original'
    image_path: str | None = None
    raw_path: str | None = None          # 原始文件（视频抽帧用；iCloud-only 时为 None）
    burst: bool = False
    burst_uuid: str | None = None
    exif: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.uuid


def _place_name(p) -> str | None:
    """Photos 自带反查的中文地名（实测 62% 的照片有值），直接白送。"""
    try:
        pl = p.place
    except Exception:
        return None
    if not pl:
        return None
    for attr in ("name", "locality", "sub_administrative_area", "administrative_area",
                 "country"):
        v = getattr(pl, attr, None)
        if v:
            return str(v)
    return None


def pick_image(p) -> tuple[str, str] | None:
    """返回 (source, path)。优先本地缩略图，其次原图，都没有返回 None。"""
    try:
        derivs = p.path_derivatives
    except Exception:
        derivs = None
    if derivs:
        best = max(derivs, key=lambda f: Path(f).stat().st_size if Path(f).exists() else 0)
        if Path(best).exists():
            return "derivative", str(best)
    try:
        if p.path and Path(p.path).exists():
            return "original", str(p.path)
    except Exception:
        pass
    return None


def to_asset(p) -> Asset:
    lat = lon = None
    try:
        if p.latitude is not None and p.longitude is not None:
            lat, lon = float(p.latitude), float(p.longitude)
    except Exception:
        pass
    src = pick_image(p)
    try:
        wh = (p.original_width or 0, p.original_height or 0)
    except Exception:
        wh = (0, 0)
    return Asset(
        uuid=p.uuid,
        filename=p.original_filename or "",
        date=p.date,
        date_added=getattr(p, "date_added", None),
        latitude=lat,
        longitude=lon,
        place_name=_place_name(p),
        width=wh[0], height=wh[1],
        ismovie=bool(getattr(p, "ismovie", False)),
        uti=getattr(p, "uti", "") or "",
        image_source=src[0] if src else None,
        image_path=src[1] if src else None,
        raw_path=(p.path if getattr(p, "ismovie", False) else None),
        burst=bool(getattr(p, "burst", False)),
        burst_uuid=getattr(p, "burst_key", None),
    )


def iter_assets() -> Iterator[Asset]:
    db = open_library()
    photos = db.photos()
    log.info("照片库载入：%d 张资产", len(photos))
    for p in photos:
        yield to_asset(p)


def should_skip(a: Asset) -> str | None:
    """返回跳过原因，None 表示保留。"""
    if a.image_path is None:
        return "no_local_image"
    if (a.width or 0) * (a.height or 0) < config.MIN_PIXELS and not a.ismovie:
        return "too_small"
    if config.SKIP_PNG_SCREENSHOT and (a.filename or "").lower().endswith(".png") \
            and not a.latitude:
        return "likely_screenshot"
    return None


# ------------------------------------------------------------------ 视频
def extract_video_frames(path: str, max_frames: int = 8) -> list[tuple[float, str]]:
    """返回 [(秒偏移, 帧文件路径)]。短视频 1fps 抽帧，长视频只抽关键帧。"""
    out: list[tuple[float, str]] = []
    src = Path(path)
    config.FRAME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(src)],
            capture_output=True, text=True, timeout=30,
        )
        duration = float((probe.stdout or "0").strip() or 0)
    except Exception:
        duration = 0

    stem = src.stem
    target = config.FRAME_DIR / stem
    target.mkdir(exist_ok=True)
    if duration <= 30:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
               "-vf", "fps=1", "-q:v", "4", str(target / "f_%04d.jpg")]
    else:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-skip_frame", "nokey",
               "-i", str(src), "-vsync", "0", "-frame_pts", "1", "-q:v", "4",
               str(target / "f_%04d.jpg")]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180)
    except Exception as e:
        log.warning("视频抽帧失败 %s: %s", path, e)
        return out
    frames = sorted(target.glob("*.jpg"))
    step = max(1, len(frames) // max_frames)
    for i, f in enumerate(frames[::step][:max_frames]):
        out.append((float(i * step), str(f)))
    return out
