"""家庭共享相册：用户体系 + 相册 + 云端照片（P5）。

设计见 docs/家庭共享相册-SPEC.md。三表：users / albums / cloud_photos，
不影响现有 6 表。认证：每用户一把 API Key（32 hex）。
"""
from __future__ import annotations

import secrets
from pathlib import Path

from . import store
from .store import conn_ctx

# ---------------------------------------------------------------- DDL
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
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cloud_photos (
  id INTEGER PRIMARY KEY,
  album_id INTEGER NOT NULL REFERENCES albums(id),
  uploader_id INTEGER NOT NULL REFERENCES users(id),
  filename TEXT NOT NULL,
  orig_name TEXT,
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
CREATE INDEX IF NOT EXISTS idx_cp_album ON cloud_photos(album_id);
CREATE INDEX IF NOT EXISTS idx_cp_uploader ON cloud_photos(uploader_id);
"""

CLOUD_DIR = Path(store.config.DATA_DIR) / "cloud"
CLOUD_THUMBS = CLOUD_DIR / "thumbs"
CLOUD_ORIG = CLOUD_DIR / "orig"


def init_sharing():
    """建表 + 确保存储目录 + 创建 owner 账号（幂等）。"""
    CLOUD_THUMBS.mkdir(parents=True, exist_ok=True)
    CLOUD_ORIG.mkdir(parents=True, exist_ok=True)
    with conn_ctx() as con:
        con.executescript(DDL)
        # owner 账号：用现有 data/.api_key 作为 owner 的 key
        owner_key = _read_owner_key()
        row = con.execute("SELECT id FROM users WHERE role='owner'").fetchone()
        if not row:
            con.execute(
                "INSERT INTO users(username, display_name, api_key, role) VALUES(?,?,?,'owner')",
                ("owner", "我", owner_key))
    return True


def _read_owner_key() -> str:
    """owner 的 API Key 复用现有 data/.api_key，保持一致。"""
    key_file = Path(store.config.DATA_DIR) / ".api_key"
    if key_file.exists():
        return key_file.read_text().strip()
    return "dev-key"


# ---------------------------------------------------------------- 认证
def get_user_by_key(api_key: str) -> dict | None:
    with conn_ctx(readonly=True) as con:
        r = con.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)).fetchone()
        return dict(r) if r else None


def create_invite(owner_key: str) -> dict | None:
    """owner 生成一次性邀请码。"""
    owner = get_user_by_key(owner_key)
    if not owner or owner["role"] != "owner":
        return None
    code = secrets.token_hex(8)
    with conn_ctx() as con:
        con.execute(
            "INSERT INTO users(username, display_name, api_key, role, invite_code) "
            "VALUES(?,?,?,?,?)",
            (f"invite-{code}", "（待注册）", f"pending-{code}", "member", code))
    return {"invite_code": code}


def register_with_invite(invite_code: str, display_name: str) -> dict | None:
    """家人用邀请码注册，返回新用户的 API Key（仅一次）。"""
    with conn_ctx() as con:
        r = con.execute(
            "SELECT id FROM users WHERE invite_code = ?", (invite_code,)).fetchone()
        if not r:
            return None
        api_key = secrets.token_hex(32)
        username = display_name.lower().replace(" ", "-")
        con.execute(
            "UPDATE users SET username=?, display_name=?, api_key=?, invite_code=NULL "
            "WHERE id = ?",
            (username, display_name, api_key, r["id"]))
        return {"user_id": r["id"], "username": username,
                "display_name": display_name, "api_key": api_key}


# ---------------------------------------------------------------- 相册
def list_albums(user: dict) -> list[dict]:
    """owner 看全部，member 看自己的。"""
    with conn_ctx(readonly=True) as con:
        if user["role"] == "owner":
            rows = con.execute(
                "SELECT a.*, u.display_name owner_name, "
                "  (SELECT COUNT(*) FROM cloud_photos p WHERE p.album_id=a.id) photo_count "
                "FROM albums a JOIN users u ON u.id=a.owner_id ORDER BY a.id DESC").fetchall()
        else:
            rows = con.execute(
                "SELECT a.*, u.display_name owner_name, "
                "  (SELECT COUNT(*) FROM cloud_photos p WHERE p.album_id=a.id) photo_count "
                "FROM albums a JOIN users u ON u.id=a.owner_id "
                "WHERE a.owner_id=? ORDER BY a.id DESC", (user["id"],)).fetchall()
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
            return None
        return dict(r)


# ---------------------------------------------------------------- 照片
def add_cloud_photo(user: dict, album_id: int, **kw) -> dict:
    with conn_ctx() as con:
        cur = con.execute(
            "INSERT INTO cloud_photos(album_id, uploader_id, filename, orig_name, "
            "  size_bytes, width, height, shot_at, media_type, exif_json, "
            "  thumb_path, orig_path, sync_status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'uploaded')",
            (album_id, user["id"], kw["filename"], kw.get("orig_name"),
             kw.get("size_bytes"), kw.get("width"), kw.get("height"),
             kw.get("shot_at"), kw.get("media_type", "image"),
             kw.get("exif_json"), kw.get("thumb_path"), kw.get("orig_path")))
        return {"id": cur.lastrowid}


def list_cloud_photos(album_id: int, user: dict, limit: int = 200,
                      offset: int = 0) -> list[dict] | None:
    if not get_album(album_id, user):
        return None
    with conn_ctx(readonly=True) as con:
        rows = con.execute(
            "SELECT p.*, u.display_name uploader_name FROM cloud_photos p "
            "JOIN users u ON u.id=p.uploader_id "
            "WHERE p.album_id=? ORDER BY p.id DESC LIMIT ? OFFSET ?",
            (album_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def get_cloud_photo(photo_id: int, user: dict) -> dict | None:
    with conn_ctx(readonly=True) as con:
        r = con.execute("SELECT * FROM cloud_photos WHERE id=?", (photo_id,)).fetchone()
        if not r:
            return None
        if not get_album(r["album_id"], user):
            return None
        return dict(r)


def delete_cloud_photo(photo_id: int, user: dict) -> bool:
    p = get_cloud_photo(photo_id, user)
    if not p:
        return False
    if user["role"] != "owner" and p["uploader_id"] != user["id"]:
        return False
    with conn_ctx() as con:
        con.execute("DELETE FROM cloud_photos WHERE id=?", (photo_id,))
    # 删文件
    for rel in (p.get("thumb_path"), p.get("orig_path")):
        if rel:
            f = CLOUD_DIR / rel
            if f.exists():
                f.unlink()
    return True


# ---------------------------------------------------------------- 同步
def sync_pull_manifest(user: dict, album_id: int) -> list[dict] | None:
    """列出云端相册照片清单（sync pull 用）。"""
    return list_cloud_photos(album_id, user, limit=10000)


def sync_mark(photo_id: int, status: str) -> None:
    with conn_ctx() as con:
        con.execute("UPDATE cloud_photos SET sync_status=? WHERE id=?",
                    (status, photo_id))
