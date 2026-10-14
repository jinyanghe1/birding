"""家庭组（groups）模型测试（路线 C 重构 · TDD：红）。

覆盖：
- init_sharing 创建默认组 + v2 库迁移回填 group_id
- 邀请码绑定组（而非一次性 album_ids）；邀请后新相册对组内自动可见
- 相册/照片可见性按组判定
- API 读范围（匿名=所有者数据 / member=自己 / owner=全部）
- 上传归属当前用户
"""
import io
import sqlite3
import sys
import types

import pytest
from PIL import Image

from test_api import TEST_KEY, env, client, owner_headers, make_jpeg  # noqa: F401

from birdscan import sharing_v2 as sharing
from birdscan import store


# ------------------------------------------------------------------ 工具
V2_NO_GROUP_DDL = """
CREATE TABLE users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL, api_key TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL DEFAULT 'member', invite_code TEXT,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE albums (
  id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL, description TEXT, cover_photo_id INTEGER,
  is_default BOLEAN DEFAULT 0, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE album_photos (
  id INTEGER PRIMARY KEY, album_id INTEGER NOT NULL, photo_id INTEGER NOT NULL,
  added_at TEXT DEFAULT (datetime('now')), UNIQUE(album_id, photo_id));
CREATE TABLE cloud_photos (
  id INTEGER PRIMARY KEY, uploader_id INTEGER NOT NULL REFERENCES users(id),
  filename TEXT NOT NULL, orig_name TEXT, file_hash TEXT UNIQUE NOT NULL,
  size_bytes INTEGER, width INTEGER, height INTEGER, shot_at TEXT,
  media_type TEXT, exif_json TEXT, species_id INTEGER, thumb_path TEXT,
  orig_path TEXT, sync_status TEXT DEFAULT 'local_only',
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE invites (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id), album_ids TEXT NOT NULL,
  max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')), expires_at TEXT);
"""


def _fresh_env(tmp_path, monkeypatch):
    """临时 DB + store/sharing 初始化，返回 db 路径。"""
    from birdscan import config
    data = tmp_path / "data"
    (data / "thumbs").mkdir(parents=True)
    db = data / "birds.db"
    monkeypatch.delenv("BIRDING_API_KEY", raising=False)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(sharing, "CLOUD_DIR", data / "cloud")
    monkeypatch.setattr(sharing, "CLOUD_THUMBS", data / "cloud" / "thumbs")
    monkeypatch.setattr(sharing, "CLOUD_ORIG", data / "cloud" / "orig")
    (data / ".api_key").write_text(TEST_KEY)
    store.init_db()
    sharing.init_sharing()
    return db


def _q(db, sql, args=()):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql, args)]
    con.close()
    return rows


# ------------------------------------------------------------------ 单元：初始化与迁移
def test_init_creates_default_group(tmp_path, monkeypatch):
    db = _fresh_env(tmp_path, monkeypatch)
    groups = _q(db, "SELECT * FROM groups")
    assert len(groups) == 1 and groups[0]["name"] == "我的家庭"
    owner = _q(db, "SELECT * FROM users WHERE role='owner'")[0]
    assert owner["group_id"] == groups[0]["id"], "owner 应加入默认组"


def test_migration_backfills_group_ids(tmp_path, monkeypatch):
    """v2 形态库（无 group_id 列）迁移：用户/相册/邀请全部回填组。"""
    from birdscan import config
    data = tmp_path / "data"
    (data / "cloud" / "orig").mkdir(parents=True)
    (data / "cloud" / "thumbs").mkdir(parents=True)
    db = data / "birds.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(sharing, "CLOUD_DIR", data / "cloud")
    monkeypatch.setattr(sharing, "CLOUD_THUMBS", data / "cloud" / "thumbs")
    monkeypatch.setattr(sharing, "CLOUD_ORIG", data / "cloud" / "orig")
    monkeypatch.delenv("BIRDING_API_KEY", raising=False)
    # 造 v2 库：无 groups 表、无 group_id 列
    con = sqlite3.connect(db)
    con.executescript(V2_NO_GROUP_DDL)
    con.execute("INSERT INTO users(username, display_name, api_key, role) "
                "VALUES('owner','我','k-owner','owner')")
    con.execute("INSERT INTO users(username, display_name, api_key, role, invite_code) "
                "VALUES('mama','妈妈','k-mama','member','abc123')")
    con.execute("INSERT INTO albums(owner_id, name, is_default) VALUES(1,'物种墙',1)")
    con.execute("INSERT INTO invites(code, created_by, album_ids) VALUES('abc123',1,'[1]')")
    con.commit()
    con.close()

    sharing.init_sharing()

    groups = _q(db, "SELECT * FROM groups")
    assert len(groups) == 1, "应种子创建默认组"
    gid = groups[0]["id"]
    users = _q(db, "SELECT username, group_id FROM users")
    assert all(u["group_id"] == gid for u in users), "存量用户应回填组"
    albums = _q(db, "SELECT name, group_id FROM albums")
    assert all(a["group_id"] == gid for a in albums), "存量相册应回填组"
    invites = _q(db, "SELECT code, group_id FROM invites")
    assert all(i["group_id"] == gid for i in invites), "存量邀请应回填组"


# ------------------------------------------------------------------ 单元：邀请绑定组
def test_invite_binds_group_and_new_album_auto_visible(tmp_path, monkeypatch):
    db = _fresh_env(tmp_path, monkeypatch)
    owner = sharing.get_user_by_key(TEST_KEY)
    gid = owner["group_id"]
    inv = sharing.create_invite(TEST_KEY, gid, max_uses=1)
    assert inv and inv["group_id"] == gid
    r = sharing.use_invite(inv["code"], "爸爸")
    member = sharing.get_user_by_key(r["api_key"])
    assert member["group_id"] == gid, "用码注册后应加入邀请的组"
    # 邀请生效后新建的相册，对组内成员自动可见（无需重新邀请）
    aid = sharing.create_album(owner, "后来建的相册")["id"]
    assert sharing.get_album(aid, member) is not None, "组内新相册应自动可见"
    # 组外用户不可见
    outsider = {"id": 999, "role": "member", "group_id": 9999}
    assert sharing.get_album(aid, outsider) is None


def test_create_album_defaults_to_user_group(tmp_path, monkeypatch):
    db = _fresh_env(tmp_path, monkeypatch)
    owner = sharing.get_user_by_key(TEST_KEY)
    aid = sharing.create_album(owner, "默认组相册")["id"]
    row = _q(db, "SELECT group_id FROM albums WHERE id=?", (aid,))[0]
    assert row["group_id"] == owner["group_id"]


def test_cloud_photo_visibility_by_group(tmp_path, monkeypatch):
    db = _fresh_env(tmp_path, monkeypatch)
    owner = sharing.get_user_by_key(TEST_KEY)
    aid = sharing.create_album(owner, "照片相册")["id"]
    (sharing.CLOUD_ORIG / "t.jpg").write_bytes(make_jpeg())
    rec = sharing.add_cloud_photo(owner, aid, "hash-vis-1",
                                  filename="t.jpg", orig_name="t.jpg",
                                  thumb_path="thumbs/t.jpg", orig_path="orig/t.jpg")
    member = {"id": 50, "role": "member", "group_id": owner["group_id"]}
    outsider = {"id": 51, "role": "member", "group_id": 9999}
    assert sharing.get_cloud_photo(rec["id"], member) is not None, "组内成员可见"
    assert sharing.get_cloud_photo(rec["id"], outsider) is None, "组外不可见"


# ------------------------------------------------------------------ API：读范围
def _mk_member(db):
    con = sqlite3.connect(db)
    cur = con.execute(
        "INSERT INTO users(username, display_name, api_key, role) VALUES(?,?,?,'member')",
        ("member1", "成员一", "key-member1"))
    con.commit()
    uid = cur.lastrowid
    con.close()
    return uid, "key-member1"


def test_api_read_scope(client, owner_headers, env):  # noqa: F811
    """匿名=所有者数据；member=自己；owner=全部。"""
    _, db, _ = env
    owner = sharing.get_user_by_key(TEST_KEY)
    mid, mkey = _mk_member(db)
    sid = store.upsert_species("范围验证鸟")
    store.add_observation(sid, "2026-10-01", None, None, None, "甲地", None,
                          None, 0.9, "test", None, owner_id=owner["id"])
    store.add_observation(sid, "2026-10-02", None, None, None, "乙地", None,
                          None, 0.9, "test", None, owner_id=mid)
    anon = client.get("/api/stats").json()
    mem = client.get("/api/stats", headers={"X-API-Key": mkey}).json()
    own = client.get("/api/stats", headers=owner_headers).json()
    assert anon["observations"] == 1, "匿名应只看所有者数据"
    assert mem["observations"] == 1, "成员应只看自己数据"
    assert own["observations"] == 2, "所有者看全部"
    # 物种列表同样分范围
    sp_mem = client.get("/api/species", headers={"X-API-Key": mkey}).json()
    assert all(s["common_name_cn"] == "范围验证鸟" for s in sp_mem)


def test_api_upload_attributes_to_current_user(client, owner_headers, env,  # noqa: F811
                                               monkeypatch):
    """上传（加新）归属当前登录用户，而非一律归 owner。"""
    _, db, _ = env
    mid, mkey = _mk_member(db)
    # stub 重依赖（CI 不装 onnxruntime/numpy）
    cstub = types.ModuleType("birdscan.classifier")
    cstub.identify_file_tta = lambda p, topk=2: {
        "is_bird": True, "box_conf": 0.9,
        "candidates": [{"common_name_cn": "上传归属鸟", "confidence": 0.88}]}
    monkeypatch.setitem(sys.modules, "birdscan.classifier", cstub)
    pstub = types.ModuleType("birdscan.pipeline")

    def _fake_thumb(src, uid):
        from pathlib import Path
        fp = Path(store.config.THUMB_DIR) / f"{uid}.jpg"
        Image.new("RGB", (60, 40), (1, 2, 3)).save(fp, "JPEG")
        return str(fp)
    pstub.make_thumb = _fake_thumb
    monkeypatch.setitem(sys.modules, "birdscan.pipeline", pstub)

    r = client.post("/api/upload",
                    headers={"X-API-Key": mkey},
                    files={"file": ("u.jpg", make_jpeg(), "image/jpeg")},
                    data={"taken_at": "2026-10-14 10:00:00", "place_name": "测试"})
    j = r.json()
    assert j.get("ok"), f"成员上传应成功: {j}"
    row = _q(db, "SELECT owner_id FROM observations ORDER BY id DESC LIMIT 1")[0]
    assert row["owner_id"] == mid, "上传的观测应归属当前用户"
    # 成员的统计里能看到这条
    mem = client.get("/api/stats", headers={"X-API-Key": mkey}).json()
    assert mem["observations"] >= 1


# ------------------------------------------------------------------ API：组邀请流
def test_api_group_invite_flow(client, owner_headers, env):  # noqa: F811
    """owner 按组发邀请 → 成员注册 → 看见组内全部相册（含后建的）。"""
    _, db, _ = env
    owner = sharing.get_user_by_key(TEST_KEY)
    gid = owner["group_id"]
    r = client.post("/api/invites", headers=owner_headers,
                    json={"group_id": gid, "max_uses": 1})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = client.post(f"/api/invites/{code}/use", json={"display_name": "组内成员"})
    assert r.status_code == 200
    mkey = r.json()["api_key"]
    mh = {"X-API-Key": mkey}
    # 先建一本新相册（邀请之后），成员也应可见
    aid = client.post("/api/albums", headers=owner_headers,
                      json={"name": "邀请后新相册"}).json()["id"]
    albums = client.get("/api/albums", headers=mh).json()
    names = {a["name"] for a in albums}
    assert "物种墙" in names and "邀请后新相册" in names, f"组内相册不全: {names}"
    # 成员能读到该相册照片列表（组权限）
    r = client.get(f"/api/albums/{aid}/photos", headers=mh)
    assert r.status_code == 200
