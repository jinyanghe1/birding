"""接口回归测试：TestClient + 临时库，不碰真实数据与照片库。

单用户模型（2026-11-15 回滚家庭共享后）：
- 读操作匿名可访问
- 写操作（删除/复核/上传/扫描）需要 X-API-Key
- 登录/用户/相册/邀请/同步路由全部下线（404）
- 手动上传走本地模型识别并入库（不归属用户）
"""
import io
import sys
import types

import pytest

TEST_KEY = "test-key-0123456789abcdef0123456789abcdef"


def make_jpeg(color=(200, 30, 30), size=(64, 48)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时数据目录 + 临时 DB，返回 (app, db_path, data_dir)。"""
    from birdscan import auth, config, store, web

    data = tmp_path / "data"
    (data / "thumbs").mkdir(parents=True)
    db = data / "birds.db"
    monkeypatch.delenv("BIRDING_API_KEY", raising=False)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "DATA_DIR", data)
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
def key_headers():
    return {"X-API-Key": TEST_KEY}


def _stub_pipeline(monkeypatch):
    """避免导入真实 pipeline（依赖 onnxruntime/torch，CI 不装）。"""
    stub = types.ModuleType("birdscan.pipeline")
    stub.scan = lambda **kw: {"scanned": 0, "kept": 0}
    monkeypatch.setitem(sys.modules, "birdscan.pipeline", stub)


def _stub_classifier(monkeypatch):
    """上传识别走假分类器，避免加载 onnxruntime。"""
    stub = types.ModuleType("birdscan.classifier")
    stub.identify_file_tta = lambda path, topk=2: {
        "is_bird": True,
        "candidates": [{"common_name_cn": "麻雀", "confidence": 0.9},
                        {"common_name_cn": "家麻雀", "confidence": 0.1}],
    }
    monkeypatch.setitem(sys.modules, "birdscan.classifier", stub)


# ------------------------------------------------------------------ 鉴权基线
def test_scan_requires_auth(client, monkeypatch):
    _stub_pipeline(monkeypatch)
    r = client.post("/api/scan")
    assert r.status_code == 401, "/api/scan 必须鉴权"
    r = client.post("/api/scan", headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200 and r.json()["ok"]


def test_anonymous_can_read(client):
    """读操作匿名开放（单用户模型）。"""
    for url in ("/api/stats", "/api/species", "/api/calendar",
                "/api/places", "/api/timeline", "/api/hours",
                "/api/review/suspect", "/api/queue"):
        r = client.get(url)
        assert r.status_code == 200, f"{url} 应匿名可读，实际 {r.status_code}"


def test_write_requires_key(client):
    """写操作无 key 一律 401。"""
    assert client.delete("/api/photo/some-uuid").status_code == 401
    assert client.delete("/api/observation/1").status_code == 401
    assert client.post("/api/review/not-bird", params={"obs_id": 1}).status_code == 401
    assert client.post("/api/review/reassign",
                       params={"obs_id": 1, "new_species_cn": "麻雀"}).status_code == 401
    assert client.post("/api/upload").status_code == 401


# ------------------------------------------------------------------ 共享路由下线
def test_sharing_routes_are_gone(client):
    """家庭共享回滚：登录/相册/邀请/组/同步路由全部 404。"""
    gone = [
        ("POST", "/api/auth/session"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/albums"),
        ("POST", "/api/albums"),
        ("GET", "/api/groups"),
        ("POST", "/api/invites"),
        ("GET", "/api/invites/somecode"),
        ("POST", "/api/sync/pull"),
        ("POST", "/api/sync/push"),
    ]
    for method, url in gone:
        r = client.request(method, url)
        assert r.status_code == 404, f"{method} {url} 应已下线（404），实际 {r.status_code}"


# ------------------------------------------------------------------ 写操作全链路（带 key）
def test_delete_observation_with_key(client, key_headers, env):
    _, db, _ = env
    from birdscan import store
    sid = store.upsert_species("白鹭")
    oid = store.add_observation(sid, "2026-11-15", "08:00", None, None,
                                "测试地", None, None, 0.9, "test", None)
    r = client.delete(f"/api/observation/{oid}", headers=key_headers)
    assert r.status_code == 200 and r.json()["ok"]
    r = client.delete(f"/api/observation/{oid}", headers=key_headers)
    assert r.status_code == 404, "重复删除应 404"


def test_upload_with_key(client, key_headers, monkeypatch, env):
    """手动上传：鉴权 + 识别入库 + 观测归属无用户概念。"""
    _stub_classifier(monkeypatch)
    files = {"file": ("a.jpg", make_jpeg(), "image/jpeg")}
    r = client.post("/api/upload", headers=key_headers,
                    files=files, data={"place_name": "测试点"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and j["common_name_cn"] == "麻雀" and j["confidence"] > 50
    import sqlite3
    con = sqlite3.connect(env[1])
    row = con.execute(
        "SELECT o.id, o.owner_id FROM observations o "
        "JOIN species s ON s.id=o.species_id WHERE s.common_name_cn='麻雀'"
    ).fetchone()
    assert row is not None, "上传应生成观测"
    assert row[1] is None, "单用户模型：owner_id 不再写入"
    con.close()


def test_reassign_not_bird_with_key(client, key_headers, env):
    from birdscan import store
    sid = store.upsert_species("乌鸦")
    oid = store.add_observation(sid, "2026-11-15", "08:00", None, None,
                                "测试地", None, None, 0.2, "test", None)
    # 复核改判
    r = client.post("/api/review/reassign", headers=key_headers,
                    params={"obs_id": oid, "new_species_cn": "喜鹊"})
    assert r.status_code == 200 and r.json()["ok"]
    # 标记不是鸟（级联删除）
    oid2 = store.add_observation(sid, "2026-11-16", "09:00", None, None,
                                 "测试地", None, None, 0.1, "test", None)
    r = client.post("/api/review/not-bird", headers=key_headers,
                    params={"obs_id": oid2})
    assert r.status_code == 200 and r.json()["ok"]
