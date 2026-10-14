"""家庭共享相册 v2：Apple Photos 逻辑。

变更：
- 相册与邀请码解耦
- 相册-照片多对多关联（去重）
- 邀请码绑定相册权限

2026-10-14 修复轮（v1→v2 半成品补全）：
- init_sharing 增加 users 表 DDL + owner 种子（移植自 v1 sharing.py：
  fresh DB 上原先没有 users 表，相册认证直接 500）
- cloud_photos 重建表迁移：去掉 v1 残留的 album_id NOT NULL、
  file_hash 补 UNIQUE 约束；旧 album_id 数据灌入 album_photos
- 补齐 v1→v2 漏移植的 4 个函数：get_cloud_photo / delete_cloud_photo /
  sync_pull_manifest / sync_mark（原先 web.py 调用即 500）
- list_album_photos 增加 offset、limit 钳制
- use_invite 原子化（UPDATE...WHERE used_count<max_uses 防并发超用）、
  空名/重名/非法字符处理
- delete_cloud_photo：无相册引用后级联删行与磁盘文件
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path

from . import store
from .store import conn_ctx

# ---------------------------------------------------------------- DDL
# 注意：users 表必须在这里创建（v2 原版漏了，导致 fresh DB 无 users 表）
DDL = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  api_key TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL DEFAULT 'member',
  invite_code TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS albums (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  description TEXT,
  cover_photo_id INTEGER,
  is_default BOLEAN DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS album_photos (
  id INTEGER PRIMARY KEY,
  album_id INTEGER NOT NULL REFERENCES albums(id),
  photo_id INTEGER NOT NULL,
  added_at TEXT DEFAULT (datetime('now')),
  UNIQUE(album_id, photo_id)
);
CREATE TABLE IF NOT EXISTS cloud_photos (
  id INTEGER PRIMARY KEY,
  uploader_id INTEGER NOT NULL REFERENCES users(id),
  filename TEXT NOT NULL,
  orig_name TEXT,
  file_hash TEXT UNIQUE NOT NULL,
  size_bytes INTEGER,
  width INTEGER, height INTEGER,
  shot_at TEXT,
  media_type TEXT,
  exif_json TEXT,
  species_id INTEGER,
  thumb_path TEXT,
  orig_path TEXT,
  sync_status TEXT DEFAULT 'local_only',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),
  album_ids TEXT NOT NULL,
  max_uses INTEGER DEFAULT 1,
  used_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ap_album ON album_photos(album_id);
CREATE INDEX IF NOT EXISTS idx_ap_photo ON album_photos(photo_id);
"""

CLOUD_DIR = Path(store.config.DATA_DIR) / "cloud"
CLOUD_THUMBS = CLOUD_DIR / "thumbs"
CLOUD_ORIG = CLOUD_DIR / "orig"

# 同步状态白名单（sync_mark 防脏数据）
SYNC_STATUSES = {"local_only", "synced", "failed"}


def _read_owner_key() -> str:
    """owner 的 API Key：环境变量优先（生产），其次 data/.api_key（本地）。"""
    key = os.environ.get("BIRDING_API_KEY")
    if key:
        return key
    key_file = Path(store.config.DATA_DIR) / ".api_key"
    if key_file.exists():
        return key_file.read_text().strip()
    return "dev-key"


def _rebuild_cloud_photos(con) -> bool:
    """重建表迁移：v1 残留 album_id NOT NULL → 去掉；file_hash 补 UNIQUE。

    旧 album_id 关联先灌入 album_photos（多对多），再换表。
    返回是否执行了重建。
    """
    cols = [r[1] for r in con.execute("PRAGMA table_info(cloud_photos)")]
    if "album_id" not in cols:
        return False  # 已是 v2 形态
    # 旧直连关联迁到多对多表（UNIQUE(album_id,photo_id) 天然幂等）
    con.execute(
        "INSERT OR IGNORE INTO album_photos(album_id, photo_id) "
        "SELECT album_id, id FROM cloud_photos WHERE album_id IS NOT NULL")
    con.executescript("""
    DROP TABLE IF EXISTS cloud_photos_mig;
    CREATE TABLE cloud_photos_mig (
      id INTEGER PRIMARY KEY,
      uploader_id INTEGER NOT NULL REFERENCES users(id),
      filename TEXT NOT NULL,
      orig_name TEXT,
      file_hash TEXT UNIQUE NOT NULL,
      size_bytes INTEGER,
      width INTEGER, height INTEGER,
      shot_at TEXT,
      media_type TEXT,
      exif_json TEXT,
      species_id INTEGER,
      thumb_path TEXT,
      orig_path TEXT,
      sync_status TEXT DEFAULT 'local_only',
      created_at TEXT DEFAULT (datetime('now'))
    );
    INSERT INTO cloud_photos_mig
      (id, uploader_id, filename, orig_name, file_hash, size_bytes, width,
       height, shot_at, media_type, exif_json, species_id, thumb_path,
       orig_path, sync_status, created_at)
    SELECT id, uploader_id, filename, orig_name,
       COALESCE(file_hash, 'nohash-' || id),
       size_bytes, width, height, shot_at, media_type, exif_json,
       species_id, thumb_path, orig_path,
       COALESCE(sync_status, 'local_only'), created_at
    FROM cloud_photos;
    DROP TABLE cloud_photos;
    ALTER TABLE cloud_photos_mig RENAME TO cloud_photos;
    """)
    return True


def init_sharing():
    """建表 + 迁移 + 确保存储目录 + owner 种子（幂等）。"""
    CLOUD_THUMBS.mkdir(parents=True, exist_ok=True)
    CLOUD_ORIG.mkdir(parents=True, exist_ok=True)
    with conn_ctx() as con:
        # 1. v2 DDL（users/albums/album_photos/cloud_photos/invites 全部 IF NOT EXISTS）
        con.executescript(DDL)
        # 2. owner 种子：fresh DB 或老库没有 owner 行时创建
        owner = con.execute("SELECT id FROM users WHERE role='owner'").fetchone()
        if not owner:
            con.execute(
                "INSERT INTO users(username, display_name, api_key, role) "
                "VALUES(?,?,?,'owner')", ("owner", "我", _read_owner_key()))
        # 3. 老库迁移：albums 加 is_default
        try:
            con.execute("SELECT is_default FROM albums LIMIT 1")
        except sqlite3.OperationalError:
            con.execute("ALTER TABLE albums ADD COLUMN is_default BOLEAN DEFAULT 0")
        # 4. 老库迁移：cloud_photos 加 file_hash 并回填
        try:
            con.execute("SELECT file_hash FROM cloud_photos LIMIT 1")
        except sqlite3.OperationalError:
            con.execute("ALTER TABLE cloud_photos ADD COLUMN file_hash TEXT")
            rows = con.execute("SELECT id, filename FROM cloud_photos").fetchall()
            for r in rows:
                fp = CLOUD_ORIG / r["filename"]
                if fp.exists():
                    h = hashlib.sha256(fp.read_bytes()).hexdigest()
                    con.execute("UPDATE cloud_photos SET file_hash=? WHERE id=?",
                                (h, r["id"]))
        # 5. 重建表迁移：album_id NOT NULL / file_hash UNIQUE
        _rebuild_cloud_photos(con)
        # 6. 默认相册（物种墙）
        row = con.execute("SELECT id FROM albums WHERE is_default=1").fetchone()
        if not row:
            owner = con.execute(
                "SELECT id FROM users WHERE role='owner'").fetchone()
            if owner:
                con.execute(
                    "INSERT INTO albums(owner_id, name, is_default) VALUES(?,?,1)",
                    (owner["id"], "物种墙"))
    return True


# ---------------------------------------------------------------- 用户
def get_user_by_key(api_key: str) -> dict | None:
    with conn_ctx(readonly=True) as con:
        r = con.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)).fetchone()
        return dict(r) if r else None


# ---------------------------------------------------------------- 相册
def list_albums(user: dict) -> list[dict]:
    with conn_ctx(readonly=True) as con:
        if user["role"] == "owner":
            rows = con.execute(
                "SELECT a.*, u.display_name owner_name, "
                "  (SELECT COUNT(*) FROM album_photos ap WHERE ap.album_id=a.id) photo_count "
                "FROM albums a JOIN users u ON u.id=a.owner_id ORDER BY a.is_default DESC, a.id").fetchall()
        else:
            # member 只能看到被邀请的相册
            rows = con.execute(
                "SELECT a.*, u.display_name owner_name, "
                "  (SELECT COUNT(*) FROM album_photos ap WHERE ap.album_id=a.id) photo_count "
                "FROM albums a JOIN users u ON u.id=a.owner_id "
                "WHERE a.owner_id=? OR a.id IN "
                "  (SELECT json_each.value FROM invites i, json_each(i.album_ids) "
                "   WHERE i.code IN (SELECT invite_code FROM users WHERE id=?)) "
                "ORDER BY a.is_default DESC, a.id",
                (user["id"], user["id"])).fetchall()
    return [dict(r) for r in rows]


def create_album(user: dict, name: str, description: str = "") -> dict:
    name = (name or "").strip()[:50]
    if not name:
        raise ValueError("相册名不能为空")
    with conn_ctx() as con:
        cur = con.execute(
            "INSERT INTO albums(owner_id, name, description) VALUES(?,?,?)",
            (user["id"], name, description))
        return {"id": cur.lastrowid, "name": name}


def get_album(album_id: int, user: dict) -> dict | None:
    with conn_ctx(readonly=True) as con:
        r = con.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
        if not r:
            return None
        if user["role"] != "owner" and r["owner_id"] != user["id"]:
            # 检查是否有权限（被邀请）
            invited = con.execute(
                "SELECT 1 FROM invites i, json_each(i.album_ids) j "
                "WHERE j.value = ? AND i.code IN "
                "  (SELECT invite_code FROM users WHERE id=?)",
                (album_id, user["id"])).fetchone()
            if not invited:
                return None
        return dict(r)


def delete_album(album_id: int, user: dict) -> dict | None:
    """删除相册（owner 或创建者）；无相册引用的照片级联删除（行+文件）。"""
    album = get_album(album_id, user)
    if not album:
        return None
    if user["role"] != "owner" and album["owner_id"] != user["id"]:
        return None
    with conn_ctx() as con:
        con.execute("DELETE FROM album_photos WHERE album_id=?", (album_id,))
        con.execute("DELETE FROM albums WHERE id=?", (album_id,))
        # GC：不再被任何相册引用的照片 → 删行 + 删文件
        orphans = con.execute(
            "SELECT id, orig_path, thumb_path FROM cloud_photos "
            "WHERE id NOT IN (SELECT photo_id FROM album_photos)").fetchall()
        for o in orphans:
            con.execute("DELETE FROM cloud_photos WHERE id=?", (o["id"],))
            for rel in (o["orig_path"], o["thumb_path"]):
                if rel:
                    fp = CLOUD_DIR / rel
                    try:
                        if fp.exists():
                            fp.unlink()
                    except OSError:
                        pass
    return {"ok": True, "deleted_album": album["name"], "gc_photos": len(orphans)}


# ---------------------------------------------------------------- 邀请码
def create_invite(owner_key: str, album_ids: list[int], max_uses: int = 1) -> dict | None:
    owner = get_user_by_key(owner_key)
    if not owner or owner["role"] != "owner":
        return None
    try:
        album_ids = [int(a) for a in (album_ids or [])][:50]
    except (TypeError, ValueError):
        return None
    max_uses = max(1, min(int(max_uses or 1), 100))
    code = secrets.token_hex(8)
    with conn_ctx() as con:
        con.execute(
            "INSERT INTO invites(code, created_by, album_ids, max_uses) VALUES(?,?,?,?)",
            (code, owner["id"], json.dumps(album_ids), max_uses))
    return {"code": code, "album_ids": album_ids, "max_uses": max_uses}


def _unique_username(con, display_name: str) -> str:
    base = re.sub(r"[^a-z0-9-]", "", display_name.lower().replace(" ", "-")) or "member"
    base = base[:24]
    cand, n = base, 1
    while con.execute("SELECT 1 FROM users WHERE username=?", (cand,)).fetchone():
        n += 1
        cand = f"{base}-{n}"
        if n > 50:
            cand = f"{base}-{secrets.token_hex(3)}"
            break
    return cand


def use_invite(code: str, display_name: str) -> dict | None:
    """用邀请码注册。原子占用额度（防并发超用）；空名返回 None。"""
    display_name = (display_name or "").strip()[:32]
    if not display_name or not code:
        return None
    with conn_ctx() as con:
        # 原子占用：rowcount=1 才说明抢到额度（SQLite 写锁串行化）
        cur = con.execute(
            "UPDATE invites SET used_count = used_count + 1 "
            "WHERE code = ? AND used_count < max_uses", (code,))
        if cur.rowcount != 1:
            return None
        inv = con.execute("SELECT * FROM invites WHERE code=?", (code,)).fetchone()
        api_key = secrets.token_hex(32)
        username = _unique_username(con, display_name)
        cur = con.execute(
            "INSERT INTO users(username, display_name, api_key, role, invite_code) "
            "VALUES(?,?,?,?,?)",
            (username, display_name, api_key, "member", code))
        return {"user_id": cur.lastrowid, "username": username,
                "display_name": display_name, "api_key": api_key,
                "album_ids": json.loads(inv["album_ids"]) if inv else []}


# ---------------------------------------------------------------- 照片（去重核心）
def get_photo_by_hash(file_hash: str) -> dict | None:
    with conn_ctx(readonly=True) as con:
        r = con.execute(
            "SELECT * FROM cloud_photos WHERE file_hash=?", (file_hash,)).fetchone()
        return dict(r) if r else None


def add_photo_to_album(photo_id: int, album_id: int, user: dict) -> bool:
    """把照片添加到相册（去重：已存在则跳过）。"""
    with conn_ctx() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO album_photos(album_id, photo_id) VALUES(?,?)",
            (album_id, photo_id))
        return cur.rowcount == 1


def add_cloud_photo(user: dict, album_id: int, file_hash: str, **kw) -> dict:
    """添加照片（自动去重）。

    注意：插入与建关联必须在同一个连接内完成——嵌套 conn_ctx 会因
    外层未提交的写锁导致内层 BUSY（曾静默失败 30s）。
    """
    existing = get_photo_by_hash(file_hash)
    if existing:
        # 已存在：只加关联
        add_photo_to_album(existing["id"], album_id, user)
        return {"id": existing["id"], "duplicate": True}

    with conn_ctx() as con:
        cur = con.execute(
            "INSERT INTO cloud_photos(uploader_id, filename, orig_name, file_hash, "
            "  size_bytes, width, height, shot_at, media_type, exif_json, "
            "  thumb_path, orig_path) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], kw["filename"], kw.get("orig_name"), file_hash,
             kw.get("size_bytes"), kw.get("width"), kw.get("height"),
             kw.get("shot_at"), kw.get("media_type", "image"),
             kw.get("exif_json"), kw.get("thumb_path"), kw.get("orig_path")))
        photo_id = cur.lastrowid
        con.execute(
            "INSERT OR IGNORE INTO album_photos(album_id, photo_id) VALUES(?,?)",
            (album_id, photo_id))
        return {"id": photo_id, "duplicate": False}


def list_album_photos(album_id: int, user: dict, limit: int = 200,
                      offset: int = 0) -> list[dict] | None:
    if not get_album(album_id, user):
        return None
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT p.*, u.display_name uploader_name FROM cloud_photos p "
            "JOIN album_photos ap ON ap.photo_id = p.id "
            "JOIN users u ON u.id = p.uploader_id "
            "WHERE ap.album_id = ? ORDER BY p.id DESC LIMIT ? OFFSET ?",
            (album_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 单张照片（v2 补齐）
def get_cloud_photo(photo_id: int, user: dict) -> dict | None:
    """取照片；权限：owner / 上传者本人 / 任一可见相册包含它。"""
    with conn_ctx(readonly=True) as con:
        r = con.execute("SELECT * FROM cloud_photos WHERE id=?", (photo_id,)).fetchone()
        if not r:
            return None
        if user["role"] == "owner" or r["uploader_id"] == user["id"]:
            return dict(r)
        vis = con.execute(
            "SELECT 1 FROM album_photos ap JOIN albums a ON a.id=ap.album_id "
            "WHERE ap.photo_id=? AND (a.owner_id=? OR a.id IN "
            "  (SELECT json_each.value FROM invites i, json_each(i.album_ids) "
            "   WHERE i.code IN (SELECT invite_code FROM users WHERE id=?)))",
            (photo_id, user["id"], user["id"])).fetchone()
        return dict(r) if vis else None


def delete_cloud_photo(photo_id: int, user: dict) -> bool:
    """删除照片（上传者本人或 owner）：清关联 → 删行 → 删文件。"""
    with conn_ctx() as con:
        r = con.execute("SELECT * FROM cloud_photos WHERE id=?", (photo_id,)).fetchone()
        if not r:
            return False
        if user["role"] != "owner" and r["uploader_id"] != user["id"]:
            return False
        con.execute("DELETE FROM album_photos WHERE photo_id=?", (photo_id,))
        con.execute("DELETE FROM cloud_photos WHERE id=?", (photo_id,))
        for rel in (r["orig_path"], r["thumb_path"]):
            if rel:
                fp = CLOUD_DIR / rel
                try:
                    if fp.exists():
                        fp.unlink()
                except OSError:
                    pass
        return True


# ---------------------------------------------------------------- 本地↔云端同步（v2 补齐）
def sync_pull_manifest(user: dict, album_id: int,
                       limit: int = 10000) -> list[dict] | None:
    """拉取某相册的照片清单（给本地 CLI 同步用）。"""
    return list_album_photos(album_id, user, limit=limit, offset=0)


def sync_mark(photo_id: int, status: str, user: dict | None = None) -> bool:
    """标记照片同步状态；仅上传者本人或 owner 可标记。"""
    if status not in SYNC_STATUSES:
        return False
    with conn_ctx() as con:
        if user is not None:
            r = con.execute(
                "SELECT uploader_id FROM cloud_photos WHERE id=?", (photo_id,)).fetchone()
            if not r:
                return False
            if user["role"] != "owner" and r["uploader_id"] != user["id"]:
                return False
        cur = con.execute(
            "UPDATE cloud_photos SET sync_status=? WHERE id=?", (status, photo_id))
        return cur.rowcount == 1
