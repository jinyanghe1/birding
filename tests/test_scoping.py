"""per-user 数据范围测试（路线 C 重构 · TDD 第一程：红）。

覆盖：
- observations.owner_id 迁移（存量回填 owner、幂等）
- add_observation / find_observation 按用户归属
- 全部读查询支持 owner_ids 过滤（None=不过滤）
"""
import sqlite3

import pytest

from birdscan import store


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    """临时 DB + sharing 初始化（seed owner）。"""
    from birdscan import config, sharing_v2
    data = tmp_path / "data"
    (data / "thumbs").mkdir(parents=True)
    db = data / "birds.db"
    monkeypatch.delenv("BIRDING_API_KEY", raising=False)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(sharing_v2, "CLOUD_DIR", data / "cloud")
    monkeypatch.setattr(sharing_v2, "CLOUD_THUMBS", data / "cloud" / "thumbs")
    monkeypatch.setattr(sharing_v2, "CLOUD_ORIG", data / "cloud" / "orig")
    (data / ".api_key").write_text("test-key")
    store.init_db()
    sharing_v2.init_sharing()
    return db


def _owner_id(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT id FROM users WHERE role='owner'").fetchone()
    con.close()
    return r["id"]


def _add_member(db, name="妈妈"):
    con = sqlite3.connect(db)
    cur = con.execute(
        "INSERT INTO users(username, display_name, api_key, role) VALUES(?,?,?,'member')",
        (name, name, f"key-{name}"))
    con.commit()
    uid = cur.lastrowid
    con.close()
    return uid


def _mk_obs(db, owner_id, species_cn, date="2026-10-01", place="测试地",
            conf=0.9, lat=39.9, lon=116.4):
    sid = store.upsert_species(species_cn)
    return store.add_observation(
        sid, date, "08:30:00", lat, lon, place, "test", None,
        conf, "test", None, owner_id=owner_id)


# ------------------------------------------------------------------ 迁移
def test_migration_backfills_owner_id(db_env):
    """存量 observations（owner_id NULL）迁移后归属 owner；幂等。"""
    db = db_env
    oid = _owner_id(db)
    sid = store.upsert_species("迁移测试鸟")
    # 绕过 add_observation，模拟存量数据
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO observations(species_id, obs_date, confidence, identified_by) "
        "VALUES(?,?,?,?)", (sid, "2026-09-01", 0.8, "legacy"))
    con.commit()
    con.close()
    # 再跑一次 init_db 触发迁移
    store.init_db()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT owner_id FROM observations")]
    con.close()
    assert all(r["owner_id"] == oid for r in rows), "存量观测应回填为 owner"
    # 幂等：第三次 init_db 不报错且数据不变
    store.init_db()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    con.close()
    assert n == len(rows)


def test_observations_owner_index_exists(db_env):
    con = sqlite3.connect(f"file:{db_env}?mode=ro", uri=True)
    idx = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='observations'")]
    con.close()
    assert any("owner" in i for i in idx), f"缺少 owner_id 索引: {idx}"


# ------------------------------------------------------------------ 写入归属
def test_add_observation_owner_attribution(db_env):
    oid = _owner_id(db_env)
    mid = _add_member(db_env)
    sid = store.upsert_species("归属测试鸟")
    o1 = store.add_observation(sid, "2026-10-01", None, None, None, None,
                               None, None, 0.9, "test", None, owner_id=oid)
    o2 = store.add_observation(sid, "2026-10-02", None, None, None, None,
                               None, None, 0.9, "test", None, owner_id=mid)
    con = sqlite3.connect(f"file:{db_env}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    got = {r["id"]: r["owner_id"] for r in con.execute(
        "SELECT id, owner_id FROM observations")}
    con.close()
    assert got[o1] == oid and got[o2] == mid


def test_find_observation_scoped_by_owner(db_env):
    """同日同种同地：不同用户的观测不互相合并。"""
    oid = _owner_id(db_env)
    mid = _add_member(db_env)
    sid = store.upsert_species("合并测试鸟")
    o1 = store.add_observation(sid, "2026-10-01", None, None, None, "同地",
                               None, None, 0.9, "test", None, owner_id=oid)
    o2 = store.add_observation(sid, "2026-10-01", None, None, None, "同地",
                               None, None, 0.9, "test", None, owner_id=mid)
    assert store.find_observation(sid, "2026-10-01", "同地", None, None,
                                  owner_id=oid) == o1
    assert store.find_observation(sid, "2026-10-01", "同地", None, None,
                                  owner_id=mid) == o2
    # owner_id=None（不过滤）时能匹配到任意一条
    assert store.find_observation(sid, "2026-10-01", "同地", None, None,
                                  owner_id=None) in (o1, o2)


# ------------------------------------------------------------------ 读范围
def test_stats_scoped(db_env):
    oid = _owner_id(db_env)
    mid = _add_member(db_env)
    _mk_obs(db_env, oid, "统计鸟A")
    _mk_obs(db_env, mid, "统计鸟B")
    all_st = store.get_stats()
    owner_st = store.get_stats(owner_ids=[oid])
    member_st = store.get_stats(owner_ids=[mid])
    assert all_st["observations"] == 2
    assert owner_st["observations"] == 1
    assert member_st["observations"] == 1
    assert member_st["species_confident"] == 1
    assert owner_st["species_confident"] == 1


def test_species_list_scoped(db_env):
    oid = _owner_id(db_env)
    mid = _add_member(db_env)
    _mk_obs(db_env, oid, "列表鸟A")
    _mk_obs(db_env, mid, "列表鸟B")
    all_rows = store.get_species_list()
    mem_rows = store.get_species_list(owner_ids=[mid])
    names_all = {r["common_name_cn"] for r in all_rows}
    names_mem = {r["common_name_cn"] for r in mem_rows}
    assert {"列表鸟A", "列表鸟B"} <= names_all
    assert names_mem == {"列表鸟B"}


def test_species_detail_scoped(db_env):
    oid = _owner_id(db_env)
    mid = _add_member(db_env)
    _mk_obs(db_env, oid, "详情鸟A")
    _mk_obs(db_env, mid, "详情鸟B")
    sid_a = store.get_species_detail_by_name("详情鸟A")["id"]
    d_all = store.get_species_detail(sid_a)
    d_mem = store.get_species_detail(sid_a, owner_ids=[mid])
    assert d_all["count"] == 1
    assert d_mem["count"] == 0
    assert d_mem["observations"] == []


def test_read_queries_scoped(db_env):
    """日历/地点/曲线/时段/地图/迁徙/加新/存疑 全部按范围过滤。"""
    oid = _owner_id(db_env)
    mid = _add_member(db_env)
    _mk_obs(db_env, oid, "范围鸟A", place="甲地", lat=39.9, lon=116.4)
    _mk_obs(db_env, mid, "范围鸟B", place="乙地", lat=31.2, lon=121.5)
    # 日历
    cal_mem = store.get_calendar(2026, owner_ids=[mid])
    assert sum(c["count"] for c in cal_mem) == 1
    # 地点
    pl_mem = store.get_places(owner_ids=[mid])
    assert [p["place_name"] for p in pl_mem] == ["乙地"]
    # 时间线
    tl_mem = store.get_timeline(owner_ids=[mid])
    assert len(tl_mem) == 1
    # 时段
    hr_mem = store.get_hour_dist(owner_ids=[mid])
    assert sum(hr_mem) == 1
    # 地图
    mp_mem = store.get_map_points(zoom=3, owner_ids=[mid])
    assert len(mp_mem) == 1 and mp_mem[0]["sp"] == 1
    # 网格明细
    cell_mem = store.get_observations_at(31.2, 121.5, zoom=12, owner_ids=[mid])
    assert len(cell_mem) == 1
    # 迁徙
    mig_mem = store.get_species_monthly_pattern(owner_ids=[mid])
    assert {r["common_name_cn"] for r in mig_mem} == {"范围鸟B"}
    # 加新地图
    fs_mem = store.get_first_seen_map(owner_ids=[mid])
    assert {r["common_name_cn"] for r in fs_mem} == {"范围鸟B"}
    # 存疑样本（低置信）
    _mk_obs(db_env, mid, "存疑鸟C", conf=0.1)
    sus_mem = store.get_suspect_samples(owner_ids=[mid])
    assert any(r["common_name_cn"] == "存疑鸟C" for r in sus_mem)
    assert all(r["common_name_cn"] != "范围鸟A" for r in sus_mem)
