"""扫描主流程：L0 读取 -> L1 检测 -> L2 去重选片 -> 入识别队列。"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, dedup, detector, geo, photos, store

log = logging.getLogger("birdscan")

BATCH = 32          # MegaDetector 批量大小
IO_WORKERS = 4      # 清晰度/哈希的 IO 并发


def _ts(a) -> float:
    try:
        return a.date.timestamp()
    except Exception:
        return 0.0


def make_thumb(src: str, uuid: str) -> str | None:
    """生成 320px 小图，供网页秒开。"""
    try:
        from PIL import Image
        out = config.THUMB_DIR / f"{uuid}.jpg"
        if out.exists():
            return str(out)
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((320, 320))
            im.save(out, "JPEG", quality=82)
        return str(out)
    except Exception:
        return None


def scan(limit: int | None = None, force: bool = False,
         detect: bool = True) -> dict:
    """返回本次扫描统计。"""
    store.init_db()
    run_id = store.start_run()
    stats = {"new_assets": 0, "l1_passed": 0, "l2_kept": 0, "new_species": 0}

    try:
        if force:
            n = store.reset_scanned()
            log.info("强制重扫，清空水位线 %d 条", n)

        # ---------------- L0 读取
        assets = []
        for a in photos.iter_assets():
            if limit and len(assets) >= limit:
                break
            assets.append(a)
        stats["new_assets"] = len(assets)

        seen = set() if force else store.already_scanned(a.uuid for a in assets)
        todo, skipped = [], 0
        for a in assets:
            if a.uuid in seen:
                continue
            reason = photos.should_skip(a)
            if reason:
                store.mark_scanned(a.uuid, "l0_skipped", reason)
                skipped += 1
            else:
                todo.append(a)
        log.info("L0：总 %d，已扫过 %d，跳过 %d，待检测 %d",
                 len(assets), len(seen), skipped, len(todo))
        if not todo:
            store.finish_run(run_id, status="ok", **stats)
            return stats

        # ---------------- L1 检测
        passed: list = []
        confs: dict[str, float] = {}
        if detect:
            det = detector.Detector()
            log.info("L1 后端：%s（%s，imgsz=%d）",
                     det.backend, det.device, det.imgsz)
            chunks = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
            done = 0
            for chunk in chunks:
                # MPS 与 ultralytics 都非线程安全，串行提交
                results = det.detect_batch([a.image_path for a in chunk])
                for a, r in zip(chunk, results):
                    confs[a.uuid] = r.max_conf
                    if r.has_animal:
                        passed.append(a)
                    else:
                        store.mark_scanned(a.uuid, "l1_no_animal", None, r.max_conf)
                done += len(chunk)
                if done % 320 == 0 or done == len(todo):
                    log.info("  L1 进度 %d/%d，已通过 %d", done, len(todo), len(passed))
        else:
            passed = list(todo)
            for a in passed:
                confs[a.uuid] = 0.0

        stats["l1_passed"] = len(passed)
        log.info("L1：%d/%d 检出动物", len(passed), len(todo))
        if not passed:
            store.finish_run(run_id, status="ok", **stats)
            return stats

        # ---------------- L1.5 视频抽帧（新增）
        # L1 检出动物的视频，从本地视频文件抽关键帧作为额外候选。
        # iCloud-only 的视频没有本地文件，保留其海报帧（已在候选里）。
        frame_cands: list[dedup.Candidate] = []
        n_video = 0
        for a in passed:
            if not a.ismovie or not a.raw_path or not Path(a.raw_path).exists():
                continue
            n_video += 1
            try:
                frames = photos.extract_video_frames(a.raw_path, max_frames=6)
            except Exception as e:
                log.debug("抽帧失败 %s: %s", a.raw_path, e)
                continue
            for off, fpath in frames:
                from datetime import timedelta
                shot = (a.date + timedelta(seconds=off)) if a.date else None
                place, _src = geo.resolve_place(a.latitude, a.longitude, a.place_name)
                frame_cands.append(dedup.Candidate(
                    uuid=f"{a.uuid}#f{int(off)}", path=fpath,
                    shot_at=shot.isoformat() if shot else None,
                    timestamp=(a.date.timestamp() + off) if a.date else 0.0,
                    latitude=a.latitude, longitude=a.longitude, place_name=place,
                    animal_conf=confs.get(a.uuid, 0.0),
                    width=a.width, height=a.height,
                    media_type="video_frame", video_offset_sec=off,
                ))
        if frame_cands:
            log.info("L1.5：%d 个视频抽帧 -> %d 张候选", n_video, len(frame_cands))

        # ---------------- L2 连拍分组 + 去重 + 选片
        cands = []
        for a in passed:
            place, _src = geo.resolve_place(a.latitude, a.longitude, a.place_name)
            cands.append(dedup.Candidate(
                uuid=a.uuid, path=a.image_path,
                shot_at=a.date.isoformat() if a.date else None,
                timestamp=_ts(a),
                latitude=a.latitude, longitude=a.longitude, place_name=place,
                animal_conf=confs.get(a.uuid, 0.0),
                width=a.width, height=a.height,
                media_type="video" if a.ismovie else "image",
            ))
        cands.extend(frame_cands)

        with ThreadPoolExecutor(max_workers=IO_WORKERS) as ex:
            sharps = list(ex.map(lambda c: dedup.sharpness_of(c.path), cands))
            hashes = list(ex.map(lambda c: dedup.phash_of(c.path), cands))
        for c, s, h in zip(cands, sharps, hashes):
            c.sharpness, c.phash = s, h

        groups = dedup.group_bursts(cands)
        kept = dedup.select(groups)
        stats["l2_kept"] = len(kept)
        log.info("L2：%d 张 -> %d 个连拍组 -> 保留 %d 张",
                 len(cands), len(groups), len(kept))

        kept_ids = {c.uuid for c in kept}
        for c in cands:
            if c.uuid not in kept_ids:
                store.mark_scanned(c.uuid, "l2_dedup", f"group={c.burst_group or 'na'}",
                                   c.animal_conf)

        # ---------------- 入队 + 缩略图
        for c in kept:
            thumb = make_thumb(c.path, c.uuid)
            store.enqueue({
                "asset_uuid": c.uuid, "image_path": c.path,
                "shot_at": c.shot_at,
                "latitude": c.latitude, "longitude": c.longitude,
                "place_name": c.place_name, "burst_group": c.burst_group,
                "sharpness": c.sharpness, "animal_conf": c.animal_conf,
            })
            store.upsert_photo(
                c.uuid, image_path=c.path, image_source="derivative",
                shot_at=c.shot_at, media_type=c.media_type,
                video_offset_sec=c.video_offset_sec,
                sharpness=c.sharpness, phash=c.phash,
                animal_conf=c.animal_conf, thumb_cache=thumb,
            )
            store.mark_scanned(c.uuid, "l3_pending", None, c.animal_conf)

        log.info("入识别队列 %d 张", len(kept))
        store.finish_run(run_id, status="ok", **stats)
        return stats

    except Exception as e:
        log.exception("扫描失败")
        store.finish_run(run_id, status="error", error_msg=str(e)[:500], **stats)
        raise
