"""SQLite 唯一数据入口。Web 与 CLI 都只通过这里读写。"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import config

log = logging.getLogger("birdscan")

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS species (
  id               INTEGER PRIMARY KEY,
  common_name_cn   TEXT NOT NULL UNIQUE,
  common_name_en   TEXT,
  scientific_name  TEXT,
  family_cn        TEXT,
  order_cn         TEXT,
  iucn_status      TEXT,
  china_protection TEXT,
  endemic_cn       INTEGER DEFAULT 0,
  family_latin     TEXT,
  order_latin      TEXT,
  in_china         INTEGER DEFAULT -1,
  inat_id          INTEGER,
  gbif_key         INTEGER,
  wikipedia_url    TEXT,
  summary          TEXT,
  links_json       TEXT,
  created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
  id             INTEGER PRIMARY KEY,
  species_id     INTEGER NOT NULL REFERENCES species(id),
  obs_date       TEXT NOT NULL,
  obs_time       TEXT,
  latitude       REAL,
  longitude      REAL,
  place_name     TEXT,
  place_source   TEXT,
  admin_region   TEXT,
  photo_count    INTEGER DEFAULT 1,
  best_sharpness REAL,
  confidence     REAL,
  identified_by  TEXT,
  notes          TEXT,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_species ON observations(species_id);
CREATE INDEX IF NOT EXISTS idx_obs_date    ON observations(obs_date);
CREATE INDEX IF NOT EXISTS idx_obs_place   ON observations(place_name);

CREATE TABLE IF NOT EXISTS photos (
  id                INTEGER PRIMARY KEY,
  obs_id            INTEGER REFERENCES observations(id) ON DELETE CASCADE,
  asset_uuid        TEXT NOT NULL UNIQUE,
  filename          TEXT,
  shot_at           TEXT,
  media_type        TEXT,
  video_offset_sec  REAL,
  image_source      TEXT,
  image_path        TEXT,
  thumb_cache       TEXT,
  width             INTEGER,
  height            INTEGER,
  sharpness         REAL,
  phash             TEXT,
  animal_conf       REAL,
  is_representative INTEGER DEFAULT 0,
  low_res           INTEGER DEFAULT 0,
  exif_json         TEXT
);
CREATE INDEX IF NOT EXISTS idx_photo_obs   ON photos(obs_id);
CREATE INDEX IF NOT EXISTS idx_photo_phash ON photos(phash);

CREATE TABLE IF NOT EXISTS scanned_assets (
  asset_uuid  TEXT PRIMARY KEY,
  scanned_at  TEXT NOT NULL,
  stage       TEXT NOT NULL,
  skip_reason TEXT,
  animal_conf REAL
);
CREATE INDEX IF NOT EXISTS idx_scanned_at ON scanned_assets(scanned_at);

CREATE TABLE IF NOT EXISTS id_queue (
  id           INTEGER PRIMARY KEY,
  asset_uuid   TEXT NOT NULL UNIQUE,
  image_path   TEXT NOT NULL,
  shot_at      TEXT,
  latitude     REAL,
  longitude    REAL,
  place_name   TEXT,
  burst_group  TEXT,
  sharpness    REAL,
  animal_conf  REAL,
  status       TEXT DEFAULT 'pending',
  result_json  TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON id_queue(status);

CREATE TABLE IF NOT EXISTS scan_runs (
  id            INTEGER PRIMARY KEY,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  new_assets    INTEGER DEFAULT 0,
  l1_passed     INTEGER DEFAULT 0,
  l2_kept       INTEGER DEFAULT 0,
  l3_identified INTEGER DEFAULT 0,
  new_species   INTEGER DEFAULT 0,
  status        TEXT,
  error_msg     TEXT
);
"""


@contextmanager
def conn_ctx(readonly: bool = False):
    if readonly:
        uri = f"file:{config.DB_PATH}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=30)
    else:
        con = sqlite3.connect(config.DB_PATH, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    try:
        yield con
        if not readonly:
            con.commit()
    finally:
        con.close()


def init_db() -> None:
    with conn_ctx() as con:
        con.executescript(SCHEMA)
        _migrate(con)


# 老库补列：CREATE TABLE IF NOT EXISTS 不会加新字段
_EXTRA_COLS = {
    "species": [("family_latin", "TEXT"), ("order_latin", "TEXT"),
                ("in_china", "INTEGER DEFAULT -1"), ("inat_id", "INTEGER"),
                ("gbif_key", "INTEGER"), ("wikipedia_url", "TEXT"),
                ("summary", "TEXT"), ("links_json", "TEXT")],
    "photos": [("thumb_cache", "TEXT")],
}


def _migrate(con) -> None:
    for table, cols in _EXTRA_COLS.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, ddl in cols:
            if name not in have:
                try:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                except Exception as e:
                    log.debug("迁移 %s.%s 失败: %s", table, name, e)


def now_iso() -> str:
    return datetime.now(config.LOCAL_TZ).isoformat(timespec="seconds")


# ----------------------------------------------------------------- 写入
def upsert_species(
    common_name_cn: str,
    scientific_name: str | None = None,
    common_name_en: str | None = None,
    family_cn: str | None = None,
    order_cn: str | None = None,
    iucn_status: str | None = None,
    china_protection: str | None = None,
    endemic_cn: int = 0,
) -> int:
    """按中文名去重；已存在时只补全空字段。返回 species.id。"""
    with conn_ctx() as con:
        row = con.execute(
            "SELECT * FROM species WHERE common_name_cn = ?", (common_name_cn,)
        ).fetchone()
        if row:
            sid = row["id"]
            updates = {
                "scientific_name": scientific_name,
                "common_name_en": common_name_en,
                "family_cn": family_cn,
                "order_cn": order_cn,
                "iucn_status": iucn_status,
                "china_protection": china_protection,
            }
            for col, val in updates.items():
                if val and not row[col]:
                    con.execute(
                        f"UPDATE species SET {col} = ? WHERE id = ?", (val, sid)
                    )
            if endemic_cn:
                con.execute("UPDATE species SET endemic_cn = 1 WHERE id = ?", (sid,))
            return sid
        cur = con.execute(
            """INSERT INTO species (common_name_cn, common_name_en, scientific_name,
                 family_cn, order_cn, iucn_status, china_protection, endemic_cn, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                common_name_cn, common_name_en, scientific_name, family_cn,
                order_cn, iucn_status, china_protection, endemic_cn, now_iso(),
            ),
        )
        return cur.lastrowid


def add_observation(
    species_id: int,
    obs_date: str,
    obs_time: str | None,
    latitude: float | None,
    longitude: float | None,
    place_name: str | None,
    place_source: str | None,
    admin_region: str | None,
    confidence: float | None,
    identified_by: str,
    notes: str | None,
) -> int:
    with conn_ctx() as con:
        cur = con.execute(
            """INSERT INTO observations
               (species_id, obs_date, obs_time, latitude, longitude, place_name,
                place_source, admin_region, confidence, identified_by, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                species_id, obs_date, obs_time, latitude, longitude, place_name,
                place_source, admin_region, confidence, identified_by, notes, now_iso(),
            ),
        )
        return cur.lastrowid


def find_observation(
    species_id: int, obs_date: str, place_name: str | None,
    latitude: float | None, longitude: float | None,
) -> int | None:
    """同鸟种 + 同日期 + 同地点簇 -> 视为同一次观测。

    实测教训：62% 的鸟照没有地名（相机长焦头无 GPS），
    早期版本要求地点精确相等，导致同日同物种的连拍被拆成多条
    （785 条观测里虚高了 277 条）。修法：
      * 两边都有地名 -> 地名相等才合并；
      * 有 GPS -> 1km 簇合并；
      * 都没有     -> 退化为「同物种 + 同日」合并。
      观鸟场景下一天一地一物种 = 一次记录，符合直觉。
    """
    from . import geo
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT id, place_name, latitude, longitude FROM observations "
            "WHERE species_id = ? AND obs_date = ?",
            (species_id, obs_date),
        ).fetchall()
    for r in rows:
        if place_name and r["place_name"]:
            if place_name == r["place_name"]:
                return r["id"]
            continue
        if latitude is not None and r["latitude"] is not None:
            if geo.haversine_km(latitude, longitude, r["latitude"], r["longitude"]) \
                    <= config.PLACE_CLUSTER_KM:
                return r["id"]
            continue
        # 至少一边无地名且无 GPS 可比 -> 同日合并
        return r["id"]
    return None


def bump_observation(obs_id: int, sharpness: float | None) -> None:
    with conn_ctx() as con:
        con.execute(
            "UPDATE observations SET photo_count = photo_count + 1 WHERE id = ?",
            (obs_id,),
        )
        if sharpness is not None:
            con.execute(
                "UPDATE observations SET best_sharpness = MAX(COALESCE(best_sharpness,0), ?) "
                "WHERE id = ?",
                (sharpness, obs_id),
            )


def upsert_photo(asset_uuid: str, **fields) -> int:
    cols = ["asset_uuid"] + list(fields)
    ph = ",".join("?" * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in fields)
    vals = [asset_uuid] + list(fields.values())
    with conn_ctx() as con:
        con.execute(
            f"INSERT INTO photos ({','.join(cols)}) VALUES ({ph}) "
            f"ON CONFLICT(asset_uuid) DO UPDATE SET {updates}",
            vals,
        )
        row = con.execute(
            "SELECT id FROM photos WHERE asset_uuid = ?", (asset_uuid,)
        ).fetchone()
        return row["id"]


def mark_scanned(uuid: str, stage: str, reason: str | None = None,
                 animal_conf: float | None = None) -> None:
    with conn_ctx() as con:
        con.execute(
            """INSERT INTO scanned_assets (asset_uuid, scanned_at, stage, skip_reason, animal_conf)
               VALUES (?,?,?,?,?)
               ON CONFLICT(asset_uuid) DO UPDATE SET
                 scanned_at=excluded.scanned_at, stage=excluded.stage,
                 skip_reason=excluded.skip_reason, animal_conf=excluded.animal_conf""",
            (uuid, now_iso(), stage, reason, animal_conf),
        )


def already_scanned(uuids: Iterable[str]) -> set[str]:
    uuids = list(uuids)
    if not uuids:
        return set()
    with conn_ctx(readonly=True) as con:
        out = set()
        for i in range(0, len(uuids), 900):
            chunk = uuids[i:i + 900]
            ph = ",".join("?" * len(chunk))
            for r in con.execute(
                f"SELECT asset_uuid FROM scanned_assets WHERE asset_uuid IN ({ph})", chunk
            ):
                out.add(r["asset_uuid"])
        return out


def enqueue(item: dict) -> None:
    with conn_ctx() as con:
        con.execute(
            """INSERT OR IGNORE INTO id_queue
               (asset_uuid, image_path, shot_at, latitude, longitude, place_name,
                burst_group, sharpness, animal_conf, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,'pending',?)""",
            (
                item["asset_uuid"], item["image_path"], item.get("shot_at"),
                item.get("latitude"), item.get("longitude"), item.get("place_name"),
                item.get("burst_group"), item.get("sharpness"),
                item.get("animal_conf"), now_iso(),
            ),
        )


def start_run() -> int:
    with conn_ctx() as con:
        cur = con.execute(
            "INSERT INTO scan_runs (started_at, status) VALUES (?, 'running')",
            (now_iso(),),
        )
        return cur.lastrowid


def finish_run(run_id: int, **stats) -> None:
    status = stats.pop("status", "ok")
    cols = list(stats) + ["finished_at", "status"]
    vals = list(stats.values()) + [now_iso(), status]
    sets = ",".join(f"{c}=?" for c in cols)
    with conn_ctx() as con:
        con.execute(f"UPDATE scan_runs SET {sets} WHERE id = ?", vals + [run_id])


# ----------------------------------------------------------------- 查询
def get_stats(min_conf: float | None = None) -> dict:
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    ok = f"confidence >= ? AND NOT {SUSPECT_SQL.replace('o.', '')}"
    with conn_ctx(readonly=True) as con:
        species = con.execute("SELECT COUNT(*) c FROM species").fetchone()["c"]
        obs = con.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
        photos = con.execute("SELECT COUNT(*) c FROM photos").fetchone()["c"]
        places = con.execute(
            "SELECT COUNT(DISTINCT place_name) c FROM observations WHERE place_name IS NOT NULL"
        ).fetchone()["c"]
        pending = con.execute(
            "SELECT COUNT(*) c FROM id_queue WHERE status='pending'"
        ).fetchone()["c"]
        # 可信口径：高置信 且 非「中国名录外+低置信」的可疑项
        row = con.execute(
            f"""SELECT COUNT(DISTINCT o.species_id) sp, COUNT(*) ob,
                       COUNT(DISTINCT o.place_name) pl
                FROM observations o JOIN species s ON s.id = o.species_id
                WHERE o.confidence >= ? AND NOT {SUSPECT_SQL}""", (mc,)
        ).fetchone()
        low = con.execute(
            f"""SELECT COUNT(*) c FROM observations o JOIN species s ON s.id=o.species_id
                WHERE o.confidence < ? OR {SUSPECT_SQL}""", (mc,)
        ).fetchone()["c"]
        checklist = con.execute(
            "SELECT COUNT(*) c FROM species WHERE in_china = 1").fetchone()["c"]
        total_cn = con.execute(
            "SELECT COUNT(*) c FROM species WHERE in_china = 1").fetchone()["c"]
        protected = con.execute(
            """SELECT COUNT(DISTINCT o.species_id) c FROM observations o
               JOIN species s ON s.id=o.species_id
               WHERE s.china_protection IS NOT NULL AND s.china_protection != ''
                 AND o.confidence >= ?""", (mc,)).fetchone()["c"]
        this_year = datetime.now(config.LOCAL_TZ).strftime("%Y-")
        new_species_ytd = con.execute(
            "SELECT COUNT(DISTINCT species_id) c FROM observations "
            "WHERE obs_date >= (SELECT MIN(obs_date) FROM observations WHERE obs_date LIKE ?) "
            "AND obs_date LIKE ?",
            (this_year + "%", this_year + "%"),
        ).fetchone()["c"]
        last = con.execute(
            "SELECT started_at, status, new_assets, l1_passed, l2_kept FROM scan_runs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        "species": species, "observations": obs, "photos": photos,
        "places": places, "pending_identify": pending,
        "species_confident": row["sp"], "observations_confident": row["ob"],
        "places_confident": row["pl"],
        "needs_review": low,
        "in_checklist": checklist,
        "protected_species": protected,
        "min_conf": mc,
        "new_species_this_year": new_species_ytd,
        "last_scan": dict(last) if last else None,
    }


# 地理先验：不在中国名录 + 低置信 = 高度可疑的误识别。
# 实测 313 条 in_china=0 的观测里 286 条（91%）落在这个区间，可直接过滤。
# 注意 in_china=0 但高置信的要保留（海外拍摄、动物园圈养，如慕尼黑疣鼻天鹅、
# 伦敦加拿大黑雁、广州长隆企鹅），所以不能一刀切按 in_china 过滤。
SUSPECT_SQL = "(s.in_china = 0 AND o.confidence < 0.45)"


def get_species_list(order: str = "count", limit: int = 200, search: str = "",
                     min_conf: float | None = None,
                     exclude_suspect: bool = True,
                     only_suspect: bool = False) -> list[dict]:
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    order_sql = {
        "count": "cnt DESC, last_date DESC",
        "recent": "last_date DESC, cnt DESC",
        "name": "s.common_name_cn COLLATE NOCASE",
    }.get(order, "cnt DESC, last_date DESC")
    args = [mc]
    where = "WHERE o.confidence >= ?"
    if exclude_suspect and not only_suspect:
        where += f" AND NOT {SUSPECT_SQL}"
    elif only_suspect:
        where += f" AND {SUSPECT_SQL}"
    if search:
        where += (" AND (s.common_name_cn LIKE ? OR s.scientific_name LIKE ? "
                  "OR s.common_name_en LIKE ? OR s.family_cn LIKE ?)")
        args += [f"%{search}%"] * 4
    sql = f"""
      SELECT s.id, s.common_name_cn, s.scientific_name, s.common_name_en, s.family_cn,
             COUNT(o.id) AS cnt,
             MAX(o.confidence) AS best_conf,
             MIN(o.obs_date) AS first_date,
             MAX(o.obs_date) AS last_date,
             (SELECT o4.place_name FROM observations o4
                WHERE o4.species_id = s.id AND o4.place_name IS NOT NULL
                  AND o4.confidence >= ?
                GROUP BY o4.place_name ORDER BY COUNT(*) DESC LIMIT 1) AS top_place,
             (SELECT p.thumb_cache FROM photos p
                WHERE p.obs_id = (SELECT o2.id FROM observations o2
                                  WHERE o2.species_id = s.id AND o2.confidence >= ?
                                  ORDER BY o2.obs_date DESC LIMIT 1)
                  AND p.thumb_cache IS NOT NULL LIMIT 1) AS thumb,
             (SELECT p2.image_path FROM photos p2
                WHERE p2.obs_id = (SELECT o3.id FROM observations o3
                                   WHERE o3.species_id = s.id AND o3.confidence >= ?
                                   ORDER BY o3.obs_date DESC LIMIT 1)
                  AND p2.image_path IS NOT NULL
                ORDER BY p2.is_representative DESC, p2.sharpness DESC LIMIT 1) AS img
      FROM species s JOIN observations o ON o.species_id = s.id
      {where}
      GROUP BY s.id
      ORDER BY {order_sql}
      LIMIT ?
    """
    with conn_ctx(readonly=True) as con:
        rows = [dict(r) for r in con.execute(sql, args + [mc, mc, mc, limit]).fetchall()]
    # 把绝对路径替换为相对路径（thumbs/xxx.jpg），前端才能加载
    for r in rows:
        for k in ("thumb", "img"):
            p = r.get(k)
            if p and "/thumbs/" in str(p):
                r[k] = "thumbs/" + Path(p).name
    return rows


def get_species_detail(species_id: int) -> dict | None:
    with conn_ctx(readonly=True) as con:
        sp = con.execute("SELECT * FROM species WHERE id = ?", (species_id,)).fetchone()
        if not sp:
            return None
        obs = con.execute(
            "SELECT * FROM observations WHERE species_id = ? ORDER BY obs_date DESC",
            (species_id,),
        ).fetchall()
        out_obs = []
        for o in obs:
            phs = con.execute(
                "SELECT * FROM photos WHERE obs_id = ? "
                "ORDER BY is_representative DESC, sharpness DESC",
                (o["id"],),
            ).fetchall()
            d = dict(o)
            photos = []
            for p in phs:
                pd = dict(p)
                # 把绝对路径替换为相对路径
                for k in ("thumb_cache", "image_path"):
                    v = pd.get(k)
                    if v and "/thumbs/" in str(v):
                        pd[k] = "thumbs/" + Path(v).name
                photos.append(pd)
            d["photos"] = photos
            out_obs.append(d)
        return {"species": dict(sp), "observations": out_obs,
                "count": len(obs),
                "places": sorted({o["place_name"] for o in obs if o["place_name"]})}


def get_all_species() -> list[dict]:
    with conn_ctx(readonly=True) as con:
        return [dict(r) for r in con.execute("SELECT * FROM species ORDER BY id")]


def update_species(species_id: int, patch: dict) -> None:
    if not patch:
        return
    sets = ",".join(f"{k}=?" for k in patch)
    with conn_ctx() as con:
        con.execute(f"UPDATE species SET {sets} WHERE id = ?",
                    list(patch.values()) + [species_id])


def get_species_detail_by_name(name: str) -> dict | None:
    with conn_ctx(readonly=True) as con:
        r = con.execute(
            "SELECT id FROM species WHERE common_name_cn = ? "
            "OR scientific_name = ? LIMIT 1", (name, name)
        ).fetchone()
        if r:
            return get_species_detail(r["id"])
        r = con.execute(
            "SELECT id FROM species WHERE common_name_cn LIKE ? LIMIT 1",
            (f"%{name}%",),
        ).fetchone()
        if r:
            return get_species_detail(r["id"])
    return None


def get_calendar(year: int, min_conf: float | None = None) -> list[dict]:
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT obs_date, COUNT(*) c FROM observations "
            "WHERE obs_date LIKE ? AND confidence >= ? GROUP BY obs_date",
            (f"{year}-%", mc),
        ).fetchall()
    return [{"date": r["obs_date"], "count": r["c"]} for r in rows]


def get_places(limit: int = 15, min_conf: float | None = None) -> list[dict]:
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT place_name, COUNT(*) c, COUNT(DISTINCT species_id) sp "
            "FROM observations WHERE place_name IS NOT NULL AND confidence >= ? "
            "GROUP BY place_name ORDER BY c DESC LIMIT ?",
            (mc, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_timeline(min_conf: float | None = None) -> list[dict]:
    """按首次观测日期累积的种数增长曲线。"""
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT species_id, MIN(obs_date) d FROM observations "
            "WHERE confidence >= ? GROUP BY species_id ORDER BY d", (mc,)
        ).fetchall()
    out, seen, n = [], set(), 0
    for r in rows:
        if r["species_id"] in seen:
            continue
        seen.add(r["species_id"])
        n += 1
        out.append({"date": r["d"], "cumulative": n})
    return out


def get_map_points(zoom: int = 3, min_conf: float | None = None,
                   exclude_suspect: bool = True,
                   sw_lat: float | None = None, sw_lon: float | None = None,
                   ne_lat: float | None = None, ne_lon: float | None = None,
                   cell_div: int = 4) -> list[dict]:
    """按缩放级别做网格聚类，供前端地图渲染。

    cell_div 控制聚合粒度：网格边长 = 180 / 2^zoom / cell_div 度。
    Apple Photos 的行为就是缩放层级越高、聚合越散。
    """
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    cell = 180.0 / (2 ** max(0, zoom)) / max(1, cell_div)
    # 子查询里用 o3/s2 别名，SUSPECT_SQL 默认是 o./s. 前缀，需替换
    suspect_o3 = SUSPECT_SQL.replace("o.", "o3.").replace("s.", "s2.")
    args: list = [mc]
    where = "WHERE o.latitude IS NOT NULL AND o.longitude IS NOT NULL AND o.confidence >= ?"
    if exclude_suspect:
        where += f" AND NOT {SUSPECT_SQL}"
    if None not in (sw_lat, sw_lon, ne_lat, ne_lon):
        where += " AND o.latitude BETWEEN ? AND ? AND o.longitude BETWEEN ? AND ?"
        args += [sw_lat, ne_lat, sw_lon, ne_lon]
    sql = f"""
      SELECT CAST(o.latitude / ? AS INT) gx, CAST(o.longitude / ? AS INT) gy,
             COUNT(*) n, COUNT(DISTINCT o.species_id) sp,
             AVG(o.latitude) lat, AVG(o.longitude) lon,
             MAX(o.obs_date) last_date,
             (SELECT p.thumb_cache FROM observations o2 JOIN photos p ON p.obs_id = o2.id
                WHERE o2.latitude IS NOT NULL AND o2.confidence >= ?
                  AND CAST(o2.latitude / ? AS INT) = CAST(o.latitude / ? AS INT)
                  AND CAST(o2.longitude / ? AS INT) = CAST(o.longitude / ? AS INT)
                  AND p.thumb_cache IS NOT NULL
                ORDER BY o2.obs_date DESC LIMIT 1) thumb,
             (SELECT s2.common_name_cn FROM observations o3
                JOIN species s2 ON s2.id = o3.species_id
                WHERE CAST(o3.latitude / ? AS INT) = CAST(o.latitude / ? AS INT)
                  AND CAST(o3.longitude / ? AS INT) = CAST(o.longitude / ? AS INT)
                  AND o3.confidence >= ?
                  {'AND NOT ' + suspect_o3 if exclude_suspect else ''}
                GROUP BY s2.id ORDER BY COUNT(*) DESC LIMIT 1) top_species
      FROM observations o JOIN species s ON s.id = o.species_id
      {where}
      GROUP BY gx, gy
      ORDER BY n DESC
    """
    params = ([cell, cell]            # gx, gy
              + [mc]                  # thumb 子查询 conf
              + [cell] * 4            # thumb 子查询 lat/lon 网格
              + [cell] * 4            # top_species 子查询 lat/lon 网格
              + [mc]                  # top_species 子查询 conf
              + args)                 # WHERE 的 mc（+ 可选视口）
    with conn_ctx(readonly=True) as con:
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    # 把绝对路径替换为相对路径
    for r in rows:
        p = r.get("thumb")
        if p and "/thumbs/" in str(p):
            r["thumb"] = "thumbs/" + Path(p).name
    return rows


def get_observations_at(lat: float, lon: float, cell_div: int = 4,
                        zoom: int = 12, min_conf: float | None = None,
                        exclude_suspect: bool = True) -> list[dict]:
    """点开地图上的一个簇，返回该网格内的观测明细。"""
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    cell = 180.0 / (2 ** max(0, zoom)) / max(1, cell_div)
    gx, gy = int(lat / cell), int(lon / cell)
    sql = f"""
      SELECT o.id, o.obs_date, o.obs_time, o.place_name, o.confidence,
             s.id species_id, s.common_name_cn, s.scientific_name,
             (SELECT p.thumb_cache FROM photos p WHERE p.obs_id = o.id
                AND p.thumb_cache IS NOT NULL LIMIT 1) thumb,
             (SELECT p2.image_path FROM photos p2 WHERE p2.obs_id = o.id
                ORDER BY p2.is_representative DESC, p2.sharpness DESC LIMIT 1) img
      FROM observations o JOIN species s ON s.id = o.species_id
      WHERE o.latitude IS NOT NULL AND o.longitude IS NOT NULL
        AND CAST(o.latitude / ? AS INT) = ? AND CAST(o.longitude / ? AS INT) = ?
        AND o.confidence >= ?
        {'AND NOT ' + SUSPECT_SQL if exclude_suspect else ''}
      ORDER BY o.obs_date DESC LIMIT 60
    """
    with conn_ctx(readonly=True) as con:
        return [dict(r) for r in con.execute(sql, (cell, gx, cell, gy, mc)).fetchall()]


def get_hour_dist(min_conf: float | None = None) -> list[int]:
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT obs_time FROM observations "
            "WHERE obs_time IS NOT NULL AND confidence >= ?", (mc,)
        ).fetchall()
    buckets = [0] * 24
    for r in rows:
        t = r["obs_time"] or ""
        try:
            buckets[int(str(t).split(":")[0])] += 1
        except (ValueError, IndexError):
            pass
    return buckets


def get_queue(status: str = "pending", limit: int = 20) -> list[dict]:
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT * FROM id_queue WHERE status = ? ORDER BY id LIMIT ?",
            (status, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def retag_identified_by(asset_uuid: str, who: str) -> None:
    """把刚写入的观测记录的 identified_by 改标（model / agent / manual）。"""
    with conn_ctx() as con:
        con.execute(
            "UPDATE observations SET identified_by = ? WHERE id = "
            "(SELECT obs_id FROM photos WHERE asset_uuid = ?)",
            (who, asset_uuid),
        )


def get_queue_item(asset_uuid: str) -> dict | None:
    with conn_ctx(readonly=True) as con:
        r = con.execute("SELECT * FROM id_queue WHERE asset_uuid = ?", (asset_uuid,)).fetchone()
    return dict(r) if r else None


def set_queue_status(asset_uuid: str, status: str, result: dict | None = None) -> None:
    with conn_ctx() as con:
        con.execute(
            "UPDATE id_queue SET status = ?, result_json = ? WHERE asset_uuid = ?",
            (status, json.dumps(result, ensure_ascii=False) if result else None, asset_uuid),
        )


def get_low_confidence(threshold: float | None = None) -> list[dict]:
    threshold = threshold if threshold is not None else config.MIN_AGENT_CONF
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            """SELECT o.id, s.common_name_cn, o.confidence, o.obs_date, o.place_name,
                      (SELECT p.image_path FROM photos p WHERE p.obs_id=o.id
                       ORDER BY p.is_representative DESC, p.sharpness DESC LIMIT 1) img
               FROM observations o JOIN species s ON s.id = o.species_id
               WHERE o.confidence IS NULL OR o.confidence < ?
               ORDER BY o.confidence ASC NULLS FIRST LIMIT 100""",
            (threshold,),
        ).fetchall()
    return [dict(r) for r in rows]


def reset_scanned() -> int:
    with conn_ctx() as con:
        n = con.execute("SELECT COUNT(*) c FROM scanned_assets").fetchone()["c"]
        con.execute("DELETE FROM scanned_assets")
        return n


# ----------------------------------------------------------------- CRUD 删除
def delete_photo(asset_uuid: str) -> dict:
    """删除一张照片（误识别清理）。如果该照片是其观测记录的最后一张，
    观测记录也会被删除（避免空观测）。返回删除统计。"""
    with conn_ctx() as con:
        row = con.execute(
            "SELECT obs_id FROM photos WHERE asset_uuid = ?",
            (asset_uuid,)).fetchone()
        if not row:
            return {"deleted_photo": 0, "deleted_obs": 0}
        obs_id = row["obs_id"]
        con.execute("DELETE FROM photos WHERE asset_uuid = ?", (asset_uuid,))
        left = con.execute(
            "SELECT COUNT(*) FROM photos WHERE obs_id = ?", (obs_id,)).fetchone()[0]
        deleted_obs = 0
        if left == 0:
            con.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
            deleted_obs = 1
        else:
            con.execute(
                "UPDATE observations SET photo_count = ? WHERE id = ?",
                (left, obs_id))
    log.info("删除照片 %s（观测 %d，剩 %d 张）", asset_uuid, obs_id, left)
    return {"deleted_photo": 1, "deleted_obs": deleted_obs, "obs_left": left}


def delete_observation(obs_id: int) -> dict:
    """删除一条观测记录及其所有照片。"""
    with conn_ctx() as con:
        photos = con.execute(
            "SELECT COUNT(*) FROM photos WHERE obs_id = ?", (obs_id,)).fetchone()[0]
        con.execute("DELETE FROM photos WHERE obs_id = ?", (obs_id,))
        con.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
    log.info("删除观测 %d（%d 张照片）", obs_id, photos)
    return {"deleted_obs": 1, "deleted_photos": photos}


# ----------------------------------------------------------------- 复核工作流
def get_suspect_samples(limit: int = 100) -> list[dict]:
    """取存疑样本：低置信度 或 不在中国名录的观测。
    供「不是我的鸟？」复核页展示。"""
    sql = f"""
      SELECT o.id obs_id, o.obs_date, o.obs_time, o.place_name, o.confidence,
             o.identified_by, o.notes,
             s.id species_id, s.common_name_cn, s.scientific_name,
             s.in_china, s.family_cn,
             (SELECT p.asset_uuid FROM photos p WHERE p.obs_id = o.id
                ORDER BY p.is_representative DESC, p.sharpness DESC LIMIT 1) asset_uuid,
             (SELECT p2.thumb_cache FROM photos p2 WHERE p2.obs_id = o.id
                ORDER BY p2.is_representative DESC, p2.sharpness DESC LIMIT 1) thumb,
             (SELECT p3.image_path FROM photos p3 WHERE p3.obs_id = o.id
                ORDER BY p3.is_representative DESC, p3.sharpness DESC LIMIT 1) img
      FROM observations o JOIN species s ON s.id = o.species_id
      WHERE o.confidence < 0.45 OR s.in_china = 0
      ORDER BY o.confidence ASC, o.obs_date DESC
      LIMIT ?
    """
    with conn_ctx(readonly=True) as con:
        rows = [dict(r) for r in con.execute(sql, (limit,)).fetchall()]
    # 把绝对路径替换为相对路径，前端才能加载
    for r in rows:
        for k in ("thumb", "img"):
            p = r.get(k)
            if p and "/thumbs/" in str(p):
                r[k] = "thumbs/" + Path(p).name
    return rows


def mark_not_bird(obs_id: int) -> dict:
    """标记「不是鸟」：删除该观测及其照片，同步更新物种墙。"""
    return delete_observation(obs_id)


def reassign_species(obs_id: int, new_species_cn: str) -> dict:
    """标记「分类错误」：把观测改到另一个物种下。"""
    sid = upsert_species(new_species_cn)
    with conn_ctx() as con:
        # 查新物种下是否已有同日观测，有则合并
        row = con.execute(
            "SELECT obs_date FROM observations WHERE id = ?", (obs_id,)).fetchone()
        if row:
            existing = con.execute(
                "SELECT id FROM observations WHERE species_id = ? AND obs_date = ? AND id != ?",
                (sid, row["obs_date"], obs_id)).fetchone()
            if existing:
                con.execute(
                    "UPDATE photos SET obs_id = ? WHERE obs_id = ?",
                    (existing["id"], obs_id))
                con.execute("DELETE FROM observations WHERE id = ?", (obs_id,))
                n = con.execute(
                    "SELECT COUNT(*) FROM photos WHERE obs_id = ?",
                    (existing["id"],)).fetchone()[0]
                con.execute(
                    "UPDATE observations SET photo_count = ? WHERE id = ?",
                    (n, existing["id"]))
                return {"merged_into": existing["id"], "new_species": new_species_cn}
        con.execute(
            "UPDATE observations SET species_id = ?, identified_by = 'manual' WHERE id = ?",
            (sid, obs_id))
    return {"reassigned": obs_id, "new_species": new_species_cn}


# ----------------------------------------------------------------- 迁徙日历
def get_species_monthly_pattern(species_id: int | None = None,
                                min_conf: float | None = None) -> list[dict]:
    """按物种聚合历年观测的月份分布，用于迁徙日历。

    返回 [{"species_id":1,"common_name_cn":"夜鹭","month":6,"years":3,"count":5}, ...]
    """
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    where = "WHERE o.confidence >= ?"
    args = [mc]
    if species_id:
        where += " AND o.species_id = ?"
        args.append(species_id)
    sql = f"""
      SELECT o.species_id, s.common_name_cn, s.family_cn,
             CAST(substr(o.obs_date, 6, 2) AS INT) month,
             COUNT(DISTINCT substr(o.obs_date, 1, 4)) years,
             COUNT(*) count
      FROM observations o JOIN species s ON s.id = o.species_id
      {where}
      GROUP BY o.species_id, month
      ORDER BY s.common_name_cn, month
    """
    with conn_ctx(readonly=True) as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def get_first_seen_map(min_conf: float | None = None) -> list[dict]:
    """每个物种的首次观测地点，用于「加新地图」。

    返回 [{"species_id":1,"common_name_cn":"夜鹭","first_date":"2025-07-03",
            "lat":22.49,"lon":113.95,"thumb":"..."}, ...]
    """
    mc = min_conf if min_conf is not None else config.UI_MIN_CONF
    sql = f"""
      SELECT s.id species_id, s.common_name_cn, s.family_cn,
             o.obs_date first_date, o.latitude lat, o.longitude lon,
             (SELECT p.thumb_cache FROM photos p WHERE p.obs_id = o.id
                AND p.thumb_cache IS NOT NULL LIMIT 1) thumb
      FROM species s
      JOIN (
        SELECT species_id, MIN(obs_date) first_date FROM observations
        WHERE confidence >= ? GROUP BY species_id
      ) first ON first.species_id = s.id
      JOIN observations o ON o.species_id = s.id AND o.obs_date = first.first_date
      WHERE o.latitude IS NOT NULL AND o.longitude IS NOT NULL
      ORDER BY first_date DESC
    """
    with conn_ctx(readonly=True) as con:
        return [dict(r) for r in con.execute(sql, (mc,)).fetchall()]


# ----------------------------------------------------------------- 常去地点
def get_frequent_places(min_count: int = 3) -> list[dict]:
    """从观测记录提取常去地点（出现次数 >= min_count）。

    返回 [{"place_name": "...", "lat": 39.9, "lon": 116.4,
            "obs_count": 15, "species_count": 8, "last_visit": "2026-08-24"}, ...]
    """
    sql = """
      SELECT place_name, AVG(latitude) lat, AVG(longitude) lon,
             COUNT(*) obs_count, COUNT(DISTINCT species_id) species_count,
             MAX(obs_date) last_visit
      FROM observations
      WHERE place_name IS NOT NULL AND place_name != ''
      GROUP BY place_name
      HAVING COUNT(*) >= ?
      ORDER BY obs_count DESC
    """
    with conn_ctx(readonly=True) as con:
        return [dict(r) for r in con.execute(sql, (min_count,)).fetchall()]


def get_home_region() -> dict | None:
    """推断常住地：观测最多的城市。"""
    sql = """
      SELECT place_name, COUNT(*) c FROM observations
      WHERE place_name IS NOT NULL AND place_name LIKE '%, %市, %'
      GROUP BY place_name ORDER BY c DESC LIMIT 1
    """
    with conn_ctx(readonly=True) as con:
        row = con.execute(sql).fetchone()
        if not row:
            return None
        # 从 "西直门, 北京市, 中国" 提取 "北京市"
        parts = row["place_name"].split(",")
        city = parts[1].strip() if len(parts) > 1 else parts[0]
        return {"city": city, "region": row["place_name"], "count": row["c"]}
