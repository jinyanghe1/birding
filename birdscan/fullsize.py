"""为网页准备全尺寸预览图。

只读取 Mac 本地已经存在的文件，绝不触发 iCloud 批量下载：
- JPEG 原图逐字节复制，不重压缩、不缩放；
- HEIC/PNG 等转为浏览器兼容的全分辨率 JPEG；
- iCloud-only 照片回退到已有最佳衍生图，并明确记录 full_kind；
- 视频使用已抽取的帧。
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps

from . import config, store

log = logging.getLogger("birdscan.fullsize")
_SAFE_UUID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _file_stem(asset_uuid: str) -> str:
    """普通 UUID 保持可读；视频帧的 #fN 等后缀哈希为安全稳定文件名。"""
    if _SAFE_UUID.fullmatch(asset_uuid):
        return asset_uuid
    return "asset-" + hashlib.sha256(asset_uuid.encode("utf-8")).hexdigest()


def export_full(asset_uuid: str, source: str | Path, kind: str,
                full_dir: str | Path | None = None) -> dict:
    """导出一张全图；JPEG 保留原文件字节，其他格式无损尺寸转码。"""
    stem = _file_stem(asset_uuid)
    if kind not in ("original", "derivative", "frame"):
        raise ValueError(f"未知全图来源：{kind}")
    src = Path(source)
    if not src.is_file():
        raise FileNotFoundError(src)
    out_dir = Path(full_dir) if full_dir is not None else Path(config.FULL_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    if src.suffix.lower() in (".heic", ".heif"):
        fmt = "HEIF"
        width = height = None
    else:
        with Image.open(src) as probe:
            fmt = probe.format
            width, height = probe.size

    if fmt in ("JPEG", "MPO"):  # MPO 是相机多帧 JPEG，浏览器可直接显示首帧
        out = out_dir / f"{stem}.jpg"
        tmp = out_dir / f".{stem}.tmp.jpg"
        shutil.copyfile(src, tmp)
        tmp.replace(out)
    else:
        out = out_dir / f"{stem}.jpg"
        tmp = out_dir / f".{stem}.tmp.jpg"
        if fmt == "HEIF":
            # macOS 自带 sips，避免服务器/CI 额外安装 HEIF 解码依赖。
            result = subprocess.run(
                ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "95",
                 str(src), "--out", str(tmp)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode or not tmp.exists():
                raise RuntimeError(f"HEIC 转 JPEG 失败：{result.stderr.strip()}")
            with Image.open(tmp) as converted:
                width, height = converted.size
        else:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode != "RGB":
                    im = im.convert("RGB")
                im.save(tmp, "JPEG", quality=95, subsampling=0)
                width, height = im.size
        tmp.replace(out)
    return {"path": str(out), "kind": kind, "width": width, "height": height,
            "bytes": out.stat().st_size}


def prepare_fullsize(limit: int | None = None, force: bool = False) -> dict:
    """为已关联观测的照片准备全图并登记路径；夜间任务也可增量运行。"""
    store.init_db()
    sql = "SELECT * FROM photos WHERE obs_id IS NOT NULL ORDER BY id"
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    with store.conn_ctx(readonly=True) as con:
        rows = [dict(r) for r in con.execute(sql, params)]

    # PhotosDB 只读元数据；不调用 export/download_missing/use_photos_export。
    from . import photos
    wanted = {r["asset_uuid"] for r in rows}
    assets = {p.uuid: p for p in photos.open_library().photos() if p.uuid in wanted}
    stats = Counter()
    for row in rows:
        uuid = row["asset_uuid"]
        p = assets.get(uuid)
        original = Path(p.path) if p and p.path else None
        is_frame = (row.get("media_type") in ("video", "video_frame")
                    or "/frames/" in str(row.get("image_path") or ""))
        has_original = bool(original and original.is_file() and not is_frame)
        previous = Path(row["full_path"]) if row.get("full_path") else None
        if (not force and previous and previous.is_file()
                and (row.get("full_kind") in ("original", "frame")
                     or (not has_original and not is_frame))):
            stats["skipped"] += 1
            stats["bytes"] += previous.stat().st_size
            continue

        if has_original:
            source, kind = original, "original"
        else:
            source = Path(row.get("image_path") or "")
            kind = "frame" if is_frame else "derivative"
            # 手动导入的文件本身即为原图，不需要出现在 Photos 库中。
            if row.get("image_source") in ("original", "upload") and source.is_file():
                kind = "original"
        if not source.is_file():
            stats["unavailable"] += 1
            continue
        try:
            out = export_full(uuid, source, kind)
        except Exception as exc:
            # 原图解码失败时仍可让网页展示已有衍生图。
            fallback = Path(row.get("image_path") or "")
            if kind == "original" and fallback.is_file() and fallback != source:
                try:
                    out = export_full(uuid, fallback, "derivative")
                except Exception:
                    stats["errors"] += 1
                    log.warning("全图导出失败 %s: %s", uuid, exc)
                    continue
            else:
                stats["errors"] += 1
                log.warning("全图导出失败 %s: %s", uuid, exc)
                continue
        store.update_photo_fullsize(uuid, out["path"], out["kind"],
                                    out["width"], out["height"])
        stats[out["kind"]] += 1
        stats["bytes"] += out["bytes"]

    return {"total": len(rows), "original": stats["original"],
            "derivative": stats["derivative"], "frame": stats["frame"],
            "skipped": stats["skipped"], "unavailable": stats["unavailable"],
            "errors": stats["errors"], "bytes": stats["bytes"]}
