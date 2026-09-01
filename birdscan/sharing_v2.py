"""家庭共享相册 v2：Apple Photos 逻辑。

变更：
- 相册与邀请码解耦
- 相册-照片多对多关联（去重）
- 邀请码绑定相册权限
"""
from __future__ import annotations

import hashlib
import sqlite3
import json
import secrets
from pathlib import Path

from . import store
from .store import conn_ctx

# ---------------------------------------------------------------- DDL
DDL = """
CREATE TABLE IF NOT EXISTS albums (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  description TEXT,
  cover_photo_id INTEGER,
  is_default BOOLEAN DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_cp_hash ON cloud_photos(file_hash);
"""

CLOUD_DIR = Path(store.config.DATA_DIR) / "cloud"
CLOUD_THUMBS = CLOUD_DIR / "thumbs"
CLOUD_ORIG = CLOUD_DIR / "orig"


def init_sharing():
    CLOUD_THUMBS.mkdir(parents=True, exist_ok=True)
    CLOUD_ORIG.mkdir(parents=True, exist_ok=True)
    with conn_ctx() as con:
        # 迁移：albums 加 is_default 列
        try:
            con.execute("SELECT is_default FROM albums LIMIT 1")
        except sqlite3.OperationalError:
            con.execute("ALTER TABLE albums ADD COLUMN is_default BOOLEAN DEFAULT 0")
        
        # 迁移：cloud_photos 加 file_hash 列
        try:
            con.execute("SELECT file_hash FROM cloud_photos LIMIT 1")
        except sqlite3.OperationalError:
            con.execute("ALTER TABLE cloud_photos ADD COLUMN file_hash TEXT")
            # 给已有数据生成哈希
            import hashlib
            rows = con.execute("SELECT id, filename FROM cloud_photos").fetchall()
            for r in rows:
                fp = CLOUD_ORIG / r["filename"]
                if fp.exists():
                    h = hashlib.sha256(fp.read_bytes()).hexdigest()
                    con.execute("UPDATE cloud_photos SET file_hash=? WHERE id=?",
                               (h, r["id"]))
        con.executescript(DDL)
        # 确保默认相册（物种墙）
        row = con.execute(
            "SELECT id FROM albums WHERE is_default=1").fetchone()
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
                "WHERE a.id IN (SELECT json_each.value FROM invites i, json_each(i.album_ids) "
                "  WHERE i.code IN (SELECT invite_code FROM users WHERE id=?)) "
                "ORDER BY a.is_default DESC, a.id", (user["id"],)).fetchall()
    return [dict(r) for r in rows]


def create_album(user: dict, name: str, description: str = "") -> dict:
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


# ---------------------------------------------------------------- 邀请码
def create_invite(owner_key: str, album_ids: list[int], max_uses: int = 1) -> dict | None:
    owner = get_user_by_key(owner_key)
    if not owner or owner["role"] != "owner":
        return None
    code = secrets.token_hex(8)
    with conn_ctx() as con:
        con.execute(
            "INSERT INTO invites(code, created_by, album_ids, max_uses) VALUES(?,?,?,?)",
            (code, owner["id"], json.dumps(album_ids), max_uses))
    return {"code": code, "album_ids": album_ids}


def use_invite(code: str, display_name: str) -> dict | None:
    with conn_ctx() as con:
        inv = con.execute(
            "SELECT * FROM invites WHERE code=? AND used_count < max_uses",
            (code,)).fetchone()
        if not inv:
            return None
        api_key = secrets.token_hex(32)
        username = display_name.lower().replace(" ", "-")
        cur = con.execute(
            "INSERT INTO users(username, display_name, api_key, role, invite_code) "
            "VALUES(?,?,?,?,?)",
            (username, display_name, api_key, "member", code))
        con.execute("UPDATE invites SET used_count = used_count + 1 WHERE id=?",
                   (inv["id"],))
        return {"user_id": cur.lastrowid, "username": username,
                "display_name": display_name, "api_key": api_key,
                "album_ids": json.loads(inv["album_ids"])}


# ---------------------------------------------------------------- 照片（去重核心）
def get_photo_by_hash(file_hash: str) -> dict | None:
    with conn_ctx(readonly=True) as con:
        r = con.execute(
            "SELECT * FROM cloud_photos WHERE file_hash=?", (file_hash,)).fetchone()
        return dict(r) if r else None


def add_photo_to_album(photo_id: int, album_id: int, user: dict) -> bool:
    """把照片添加到相册（去重：已存在则跳过）。"""
    with conn_ctx() as con:
        try:
            con.execute(
                "INSERT INTO album_photos(album_id, photo_id) VALUES(?,?)",
                (album_id, photo_id))
            return True
        except Exception:
            return False  # 已存在


def add_cloud_photo(user: dict, album_id: int, file_hash: str, **kw) -> dict:
    """添加照片（自动去重）。"""
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
        add_photo_to_album(photo_id, album_id, user)
        return {"id": photo_id, "duplicate": False}


def list_album_photos(album_id: int, user: dict, limit: int = 200) -> list[dict] | None:
    if not get_album(album_id, user):
        return None
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT p.*, u.display_name uploader_name FROM cloud_photos p "
            "JOIN album_photos ap ON ap.photo_id = p.id "
            "JOIN users u ON u.id = p.uploader_id "
            "WHERE ap.album_id = ? ORDER BY p.id DESC LIMIT ?",
            (album_id, limit)).fetchall()
    return [dict(r) for r in rows]
