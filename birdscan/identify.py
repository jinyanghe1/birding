"""L3 识别层：队列管理 + Agent 结果落库。

识别由 Agent（视觉模型）完成，这里只负责：
  1. 从 id_queue 取待识别批次；
  2. 把 Agent 返回的结构化结果写进 species / observations / photos。

一次观测 = 同一鸟种 + 同一天 + 同一地点簇。
「次数」就是 observations 的行数，永不冗余存字段。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from . import config, geo, store

log = logging.getLogger("birdscan")


def take_batch(limit: int | None = None) -> list[dict]:
    limit = limit or config.AGENT_BATCH_SIZE
    return store.get_queue("pending", limit)


def apply_result(asset_uuid: str, result: dict) -> dict:
    """result = {
        common_name_cn, scientific_name, common_name_en,
        family_cn, order_cn, confidence, is_bird, notes
    }"""
    from . import store as _s
    rows = _s.get_queue_item(asset_uuid)
    if not rows:
        return {"ok": False, "error": "uuid 不在识别队列中"}

    is_bird = bool(result.get("is_bird", True))
    if not is_bird:
        store.mark_scanned(asset_uuid, "l3_rejected", result.get("notes") or "非鸟类")
        _s.set_queue_status(asset_uuid, "rejected", result)
        return {"ok": True, "action": "rejected"}

    name = (result.get("common_name_cn") or "").strip()
    if not name:
        _s.set_queue_status(asset_uuid, "failed", result)
        return {"ok": False, "error": "缺少 common_name_cn"}

    conf = result.get("confidence")
    conf = float(conf) if conf is not None else None

    sid = store.upsert_species(
        name,
        scientific_name=result.get("scientific_name"),
        common_name_en=result.get("common_name_en"),
        family_cn=result.get("family_cn"),
        order_cn=result.get("order_cn"),
        iucn_status=result.get("iucn_status"),
        china_protection=result.get("china_protection"),
        endemic_cn=1 if result.get("endemic_cn") else 0,
    )

    item = rows
    shot = item.get("shot_at") or ""
    obs_date = shot[:10] if len(shot) >= 10 else datetime.now(config.LOCAL_TZ).strftime("%Y-%m-%d")
    obs_time = shot[11:19] if len(shot) >= 19 else None
    lat, lon = item.get("latitude"), item.get("longitude")
    place, src = geo.resolve_place(lat, lon, item.get("place_name"))
    admin = geo.admin_region(place)

    obs_id = store.find_observation(sid, obs_date, place, lat, lon)
    if obs_id is None:
        obs_id = store.add_observation(
            sid, obs_date, obs_time, lat, lon, place, src, admin,
            conf, "agent", result.get("notes"),
        )
        action = "new_observation"
    else:
        store.bump_observation(obs_id, item.get("sharpness"))
        action = "merged"

    store.upsert_photo(
        asset_uuid,
        obs_id=obs_id,
        filename=None,
        shot_at=item.get("shot_at"),
        image_path=item.get("image_path"),
        sharpness=item.get("sharpness"),
        animal_conf=item.get("animal_conf"),
        is_representative=1,
    )
    store.mark_scanned(asset_uuid, "l3_identified", None, item.get("animal_conf"))
    _s.set_queue_status(asset_uuid, "done", result)
    return {"ok": True, "action": action, "obs_id": obs_id, "species_id": sid,
            "obs_date": obs_date, "place": place}


def apply_batch(results: list[dict]) -> dict:
    """results = [{"asset_uuid": ..., ...识别字段}, ...]"""
    out = {"ok": 0, "rejected": 0, "failed": 0, "errors": []}
    for r in results:
        uuid = r.get("asset_uuid")
        if not uuid:
            out["failed"] += 1
            out["errors"].append("缺少 asset_uuid")
            continue
        res = apply_result(uuid, r)
        if not res.get("ok"):
            out["failed"] += 1
            out["errors"].append(f"{uuid}: {res.get('error')}")
        elif res.get("action") == "rejected":
            out["rejected"] += 1
        else:
            out["ok"] += 1
    return out


def auto_identify(limit: int | None = None, min_conf: float = 0.25,
                  accept_conf: float = 0.45) -> dict:
    """用本地 ONNX 模型（YOLO 框鸟 + ConvNeXt 分类）批量给候选图打标签。

    结果一律标记 identified_by='model'；置信度 < accept_conf 的写入
    observations 但 confidence 偏低，会在 `bird review` 里出现，等人工或
    多模态模型复核。
    """
    from . import classifier

    rows = store.get_queue("pending", limit or 10 ** 9)
    out = {"total": len(rows), "bird": 0, "no_bird": 0, "accepted": 0,
           "needs_review": 0, "failed": 0}
    for i, r in enumerate(rows):
        res = classifier.identify_file(r["image_path"], topk=1)
        if not res.get("is_bird") or not res.get("candidates"):
            store.mark_scanned(r["asset_uuid"], "l3_no_bird",
                               f"box_conf={res.get('box_conf', 0):.2f}")
            store.set_queue_status(r["asset_uuid"], "done", res)
            out["no_bird"] += 1
            continue
        top = res["candidates"][0]
        payload = {
            "common_name_cn": top["common_name_cn"],
            "common_name_en": top.get("common_name_en"),
            "confidence": top["confidence"],
            "is_bird": True,
            "notes": f"本地模型识别（鸟框置信度 {res['box_conf']:.2f}），待复核",
        }
        r0 = apply_result(r["asset_uuid"], payload)
        if not r0.get("ok"):
            out["failed"] += 1
            continue
        # apply_result 里写的是 identified_by='agent'，这里改标 model
        store.retag_identified_by(r["asset_uuid"], "model")
        out["bird"] += 1
        if top["confidence"] >= accept_conf:
            out["accepted"] += 1
        else:
            out["needs_review"] += 1
        if (i + 1) % 200 == 0:
            log.info("  自动识别进度 %d/%d", i + 1, len(rows))
    return out


def prompt_for_batch(batch: list[dict]) -> str:
    """生成给 Agent 的识别提示词。抄 Merlin 的时空先验：把时间地点一起给。"""
    lines = [
        "下面是若干张候选照片，请逐张判断是否为鸟类，并识别鸟种。",
        "对每张图严格输出一行 JSON（不要 markdown 代码块、不要额外解释）：",
        '{"asset_uuid":"...","is_bird":true,"common_name_cn":"中文名",'
        '"scientific_name":"拉丁学名","confidence":0.9,"notes":"一句话辨识依据"}',
        "",
        "规则：",
        "- 不是鸟 -> is_bird:false，confidence 留空",
        "- 认不出种 -> 给科或属，如「柳莺属」「鹟科」，confidence 相应降低",
        "- confidence < 0.55 时宁可给高阶分类，也不要瞎猜种",
        "- 已给出拍摄时间和地点，请用作时空先验缩小候选范围",
        "",
    ]
    for b in batch:
        when = (b.get("shot_at") or "")[:16].replace("T", " ")
        where = b.get("place_name") or (
            f"{b['latitude']:.3f},{b['longitude']:.3f}"
            if b.get("latitude") is not None else "未知")
        lines.append(f"- {b['asset_uuid']}  path={b['image_path']}  时间={when}  地点={where}")
    return "\n".join(lines)
