"""一次性维护脚本：合并被拆散的观测记录。

早期 find_observation 要求地点精确相等，导致同日同物种的无地名观测
被拆成多条（785 条里虚高 277 条）。此脚本按新规则回填合并：
  同物种 + 同日，且（地名相等 或 GPS 1km 簇 或 至少一边无定位）。

用法：python -m birdscan.cli merge-obs [--dry-run]
"""
from __future__ import annotations

import logging

from . import config, store

log = logging.getLogger("birdscan")


def merge_observations(dry_run: bool = False) -> dict:
    from . import geo
    with store.conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT id, species_id, obs_date, place_name, latitude, longitude, "
            "photo_count, best_sharpness, confidence FROM observations "
            "ORDER BY species_id, obs_date, id"
        ).fetchall()
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        # 组键：物种 + 日期 + 地点簇（None 地名 / 无 GPS 都归为通配）
        key_place = (r["place_name"] or "").strip() or None
        groups.setdefault((r["species_id"], r["obs_date"]), []).append(dict(r))

    merged, deleted = 0, 0
    with store.conn_ctx() as con:
        for (sid, date), obs_list in groups.items():
            # 在组内按新规则聚类：地名精确相等 / GPS 簇 / 无定位互相合并
            clusters: list[list[dict]] = []
            for o in obs_list:
                placed = False
                for cl in clusters:
                    head = cl[0]
                    if o["place_name"] and head["place_name"]:
                        if o["place_name"] == head["place_name"]:
                            cl.append(o); placed = True; break
                        continue
                    if o["latitude"] is not None and head["latitude"] is not None:
                        if geo.haversine_km(o["latitude"], o["longitude"],
                                            head["latitude"],
                                            head["longitude"]) <= config.PLACE_CLUSTER_KM:
                            cl.append(o); placed = True; break
                        continue
                    cl.append(o); placed = True; break
                if not placed:
                    clusters.append([o])
            for cl in clusters:
                if len(cl) < 2:
                    continue
                # 保留置信度最高的一条，其余并入
                cl.sort(key=lambda x: -(x["confidence"] or 0))
                keep = cl[0]
                for dup in cl[1:]:
                    if dry_run:
                        deleted += 1
                        continue
                    con.execute(
                        "UPDATE photos SET obs_id = ? WHERE obs_id = ?",
                        (keep["id"], dup["id"]))
                    con.execute("DELETE FROM observations WHERE id = ?", (dup["id"],))
                    deleted += 1
                merged += 1
                if not dry_run:
                    n = con.execute(
                        "SELECT COUNT(*) FROM photos WHERE obs_id = ?",
                        (keep["id"],)).fetchone()[0]
                    con.execute(
                        "UPDATE observations SET photo_count = MAX(photo_count, ?) "
                        "WHERE id = ?", (n, keep["id"]))
    out = {"merged_groups": merged, "observations_removed": deleted,
           "dry_run": dry_run}
    log.info("合并完成：%s", out)
    return out


def infer_places(dry_run: bool = False, window_hours: float = 3.0) -> dict:
    """地点时间邻近推断：无地名的观测借用同日 ±window_hours 内
    带地名的观测的地点（观鸟一般一个半天泡在一个地方）。

    无 GPS 的相机照片（62%）借此获得地名。
    """
    from datetime import datetime, timedelta

    def parse(s):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    with store.conn_ctx(readonly=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, obs_date, obs_time, latitude, longitude, place_name "
            "FROM observations ORDER BY obs_date, obs_time")]
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["obs_date"], []).append(r)

    filled, ambiguous = 0, 0
    updates: list[tuple[str, str, int]] = []
    for date, day in by_date.items():
        known = [r for r in day if r["place_name"]]
        unknown = [r for r in day if not r["place_name"] and not r["latitude"]]
        if not known or not unknown:
            continue
        for u in unknown:
            tu = parse(f"{u['obs_date']}T{u['obs_time'] or '12:00:00'}")
            cands = []
            for k in known:
                tk = parse(f"{k['obs_date']}T{k['obs_time'] or '12:00:00'}")
                if tu and tk and abs((tu - tk).total_seconds()) <= window_hours * 3600:
                    cands.append(k)
            if not cands:
                continue
            names = {c["place_name"] for c in cands}
            if len(names) == 1:
                updates.append((cands[0]["place_name"], "inferred", u["id"]))
                filled += 1
            else:
                ambiguous += 1
    if not dry_run:
        with store.conn_ctx() as con:
            for name, src, oid in updates:
                con.execute(
                    "UPDATE observations SET place_name = ?, place_source = ? "
                    "WHERE id = ?", (name, src, oid))
    out = {"filled": filled, "ambiguous_skipped": ambiguous, "dry_run": dry_run}
    log.info("地点推断完成：%s", out)
    return out
