"""接口回归测试：TestClient + 临时库，不碰真实数据与照片库。

覆盖 2026-10-14 修复轮的 P0/P1 项：
- P0-4  POST /api/scan 鉴权
- P1-11 相册上传类型白名单
- P1-8  相册上传大小限制
- P0-2  相册全链路（上传/查看/缩略图/删除/同步）
- P1-5  邀请码额度（用完即止）
- P1-6  邀请注册空名 400
- P1-7  删相册级联 GC（行 + 文件）
- P0-3  v1→v2 重建表迁移（album_id 移除、file_hash UNIQUE、关联保留）
"""
import io
import sqlite3
import sys
import types

import pytest
from PIL import Image

TEST_KEY = "test-key-0123456789abcdef0123456789abcdef"

# v1 形态 DDL（复刻 sharing.py，用于迁移回归测试）
V1_DDL = """
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  api_key TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL DEFAULT 'member',
  invite_code TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE albums (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  description TEXT,
  cover_photo_id INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE cloud_photos (
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
"""


def make_jpeg(color=(200, 30, 30), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时数据目录 + 临时 DB + 临时 cloud 目录，返回 (app, db_path)。"""
    from birdscan import auth, config, sharing_v2, store, web

    data = tmp_path / "data"
    (data / "thumbs").mkdir(parents=True)
    db = data / "birds.db"
    monkeypatch.delenv("BIRDING_API_KEY", raising=False)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(sharing_v2, "CLOUD_DIR", data / "cloud")
    monkeypatch.setattr(sharing_v2, "CLOUD_THUMBS", data / "cloud" / "thumbs")
    monkeypatch.setattr(sharing_v2, "CLOUD_ORIG", data / "cloud" / "orig")
    monkeypatch.setattr(web, "IMPORT_DIR", data / "imports")
    monkeypatch.setattr(auth, "API_KEY", TEST_KEY)
    (data / ".api_key").write_text(TEST_KEY)
    store.init_db()
    return web.app, db, data


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient
    with TestClient(env[0]) as c:
        yield c


@pytest.fixture()
def owner_headers():
    return {"X-API-Key": TEST_KEY}


def _stub_pipeline(monkeypatch):
    """避免导入真实 pipeline（依赖 onnxruntime/torch，CI 不装）。"""
    stub = types.ModuleType("birdscan.pipeline")
    stub.scan = lambda **kw: {"scanned": 0, "kept": 0}
    monkeypatch.setitem(sys.modules, "birdscan.pipeline", stub)


def _upload(client, headers, album_id, jpeg=None, name="a.jpg"):
    files = [("files", (name, jpeg or make_jpeg(), "image/jpeg"))]
    return client.post(f"/api/albums/{album_id}/photos", headers=headers, files=files)


# ------------------------------------------------------------------ 鉴权
def test_scan_requires_auth(client, monkeypatch):
    _stub_pipeline(monkeypatch)
    r = client.post("/api/scan")
    assert r.status_code == 401, "P0-4: /api/scan 必须鉴权"
    r = client.post("/api/scan", headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200 and r.json()["ok"]


def test_owner_login_and_default_album(client, owner_headers):
    r = client.post("/api/auth/login", json={"api_key": TEST_KEY})
    assert r.status_code == 200 and r.json()["user"]["role"] == "owner"
    albums = client.get("/api/albums", headers=owner_headers).json()
    assert any(a["name"] == "物种墙" and a["is_default"] for a in albums), \
        "init_sharing 应自动创建默认相册"


def test_album_endpoints_require_key(client):
    assert client.get("/api/albums").status_code == 401
    assert client.post("/api/albums", json={"name": "x"}).status_code == 401


# ------------------------------------------------------------------ 相册全链路（P0-2）
def test_album_photo_lifecycle(client, owner_headers, env):
    _, db, data = env
    r = client.post("/api/albums", headers=owner_headers, json={"name": "测试相册"})
    assert r.status_code == 200
    album_id = r.json()["id"]

    # 上传
    r = _upload(client, owner_headers, album_id)
    assert r.status_code == 200 and r.json()[0]["ok"], r.text
    pid = r.json()[0]["id"]

    # 列表（含上传者名）
    rows = client.get(f"/api/albums/{album_id}/photos", headers=owner_headers).json()
    assert len(rows) == 1 and rows[0]["uploader_name"] == "我"

    # 查看 / 缩略图（P0-2 补齐的端点）
    assert client.get(f"/api/photos/{pid}", headers=owner_headers).status_code == 200
    thumb = client.get(f"/api/photos/{pid}/thumb", headers=owner_headers)
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert thumb.headers.get("x-content-type-options") == "nosniff"

    # 去重：同字节再上传 → duplicate
    jpeg = make_jpeg()
    r = _upload(client, owner_headers, album_id, jpeg=jpeg)
    assert r.json()[0]["duplicate"] is True

    # 同步 pull / push（P0-2 补齐的端点）
    manifest = client.post("/api/sync/pull", headers=owner_headers,
                           json={"album_id": album_id}).json()
    assert len(manifest) == 1
    push = client.post("/api/sync/push", headers=owner_headers,
                       json={"marks": {str(pid): "synced"}}).json()
    assert push["marked"] == 1 and push["failed"] == []
    # 非法 status 被拒
    push = client.post("/api/sync/push", headers=owner_headers,
                       json={"marks": {str(pid): "bogus"}}).json()
    assert push["marked"] == 0

    # 删除照片 → 404 + 文件级联删除
    filename = rows[0]["filename"]
    assert client.delete(f"/api/photos/{pid}", headers=owner_headers).status_code == 200
    assert client.get(f"/api/photos/{pid}", headers=owner_headers).status_code == 404
    assert not (data / "cloud" / "orig" / filename).exists(), "删除照片应级联删文件"


# ------------------------------------------------------------------ 上传安全（P1-8 / P1-11）
def test_upload_whitelist_and_size(client, owner_headers, monkeypatch):
    from birdscan import web
    album_id = client.post("/api/albums", headers=owner_headers,
                           json={"name": "安全测试"}).json()["id"]
    # 类型白名单：.html 被拒（存储型 XSS 防护）
    r = client.post(f"/api/albums/{album_id}/photos", headers=owner_headers,
                    files=[("files", ("x.html", b"<script>alert(1)</script>",
                                      "text/html"))])
    assert r.status_code == 200 and r.json()[0]["ok"] is False
    assert "不支持" in r.json()[0]["error"]
    # 大小限制（把上限临时降到 1KB）
    monkeypatch.setattr(web, "ALBUM_MAX_BYTES", 1024)
    big = io.BytesIO()
    Image.new("RGB", (200, 200), (1, 2, 3)).save(big, "JPEG")
    r = _upload(client, owner_headers, album_id, jpeg=big.getvalue(), name="big.jpg")
    assert r.json()[0]["ok"] is False and "过大" in r.json()[0]["error"]


# ------------------------------------------------------------------ 邀请流（P1-5 / P1-6 / P1-10）
def test_invite_flow(client, owner_headers):
    album_id = client.post("/api/albums", headers=owner_headers,
                           json={"name": "家庭相册"}).json()["id"]
    # 邀请码绑定家庭组（路线 C：入组后组内相册全可见）
    from birdscan import sharing_v2 as _sharing
    gid = _sharing.get_user_by_key(TEST_KEY)["group_id"]
    inv = client.post("/api/invites", headers=owner_headers,
                      json={"group_id": gid, "max_uses": 1})
    assert inv.status_code == 200
    code = inv.json()["code"]

    # 空名 → 400（P1-6）
    assert client.post(f"/api/invites/{code}/use",
                       json={"display_name": "   "}).status_code == 400

    # 正常注册（P1-10：端点必须是 /api/invites/{code}/use）
    r = client.post(f"/api/invites/{code}/use", json={"display_name": "妈妈"})
    assert r.status_code == 200, r.text
    mkey = r.json()["api_key"]
    mh = {"X-API-Key": mkey}

    # member 可见被邀请相册，且能上传
    malbums = client.get("/api/albums", headers=mh).json()
    assert any(a["id"] == album_id for a in malbums)
    r = _upload(client, mh, album_id, jpeg=make_jpeg((30, 100, 200)), name="mom.jpg")
    assert r.json()[0]["ok"]

    # owner 视角能看到上传者是「妈妈」
    rows = client.get(f"/api/albums/{album_id}/photos", headers=owner_headers).json()
    assert any(x["uploader_name"] == "妈妈" for x in rows)

    # 额度用尽 → 再注册 400（P1-5）
    assert client.post(f"/api/invites/{code}/use",
                       json={"display_name": "爸爸"}).status_code == 400

    # member 不能删 owner 的相册 → 403
    assert client.delete(f"/api/albums/{album_id}", headers=mh).status_code == 403

    # 重名注册不 500（username 自动加后缀）
    inv2 = client.post("/api/invites", headers=owner_headers,
                       json={"group_id": gid, "max_uses": 1}).json()["code"]
    r = client.post(f"/api/invites/{inv2}/use", json={"display_name": "妈妈"})
    assert r.status_code == 200 and r.json()["username"] != "妈妈"


# ------------------------------------------------------------------ 删相册 GC（P1-7）
def test_delete_album_gc(client, owner_headers, env):
    _, db, data = env
    album_id = client.post("/api/albums", headers=owner_headers,
                           json={"name": "GC测试"}).json()["id"]
    r = _upload(client, owner_headers, album_id)
    pid = r.json()[0]["id"]
    # 删相册 → 照片行与文件都清掉
    r = client.delete(f"/api/albums/{album_id}", headers=owner_headers)
    assert r.status_code == 200 and r.json()["gc_photos"] == 1
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM cloud_photos").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM album_photos").fetchone()[0] == 0
    con.close()
    assert not any((data / "cloud" / "orig").iterdir()), "孤儿文件应被清理"


# ------------------------------------------------------------------ v1→v2 迁移（P0-3）
def test_migration_from_v1_schema(tmp_path, monkeypatch):
    from birdscan import config, sharing_v2
    data = tmp_path / "data"
    (data / "cloud" / "orig").mkdir(parents=True)
    (data / "cloud" / "thumbs").mkdir(parents=True)
    db = data / "birds.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(sharing_v2, "CLOUD_DIR", data / "cloud")
    monkeypatch.setattr(sharing_v2, "CLOUD_THUMBS", data / "cloud" / "thumbs")
    monkeypatch.setattr(sharing_v2, "CLOUD_ORIG", data / "cloud" / "orig")
    monkeypatch.delenv("BIRDING_API_KEY", raising=False)

    # 造一个 v1 形态库：cloud_photos 带 album_id NOT NULL、无 file_hash、
    # 无 album_photos / invites 表
    con = sqlite3.connect(db)
    con.executescript(V1_DDL)
    con.execute("INSERT INTO users(username, display_name, api_key, role) "
                "VALUES('owner','我','k1','owner')")
    con.execute("INSERT INTO albums(owner_id, name) VALUES(1,'老相册')")
    jpeg = make_jpeg()
    (data / "cloud" / "orig" / "old1.jpg").write_bytes(jpeg)
    con.execute(
        "INSERT INTO cloud_photos(album_id, uploader_id, filename, orig_name, "
        "thumb_path, orig_path) VALUES(1,1,'old1.jpg','old.jpg',"
        "'thumbs/old1.jpg','orig/old1.jpg')")
    con.commit()
    con.close()

    # 执行迁移
    sharing_v2.init_sharing()

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cols = [r[1] for r in con.execute("PRAGMA table_info(cloud_photos)")]
    assert "album_id" not in cols, "album_id 应被移除（v2 上传不再写该列）"
    row = con.execute("SELECT * FROM cloud_photos").fetchone()
    assert row["file_hash"] and len(row["file_hash"]) == 64, "file_hash 应回填"
    ap = con.execute("SELECT * FROM album_photos").fetchall()
    assert len(ap) == 1 and ap[0]["album_id"] == 1 and ap[0]["photo_id"] == row["id"], \
        "旧 album_id 关联应迁入 album_photos"
    # file_hash UNIQUE 生效
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO cloud_photos(uploader_id, filename, file_hash) "
                    "VALUES(1,'x.jpg',?)", (row["file_hash"],))
    # users / invites 表存在
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "invites", "album_photos"} <= tables
    con.close()
