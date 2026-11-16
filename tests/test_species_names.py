"""物种名归一（别名）与常住地配置的回归测试。

对应用户诉求：修正时输入「玄凤 / 玄凤鹦鹉 / 鸡尾鹦鹉」必须落到同一物种，
不能各建一个；地图与推荐默认城市不能再硬编码北京。
"""
import pytest

from birdscan import locations, store


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from birdscan import config
    data = tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(config, "DB_PATH", data / "birds.db")
    monkeypatch.setattr(config, "DATA_DIR", data)
    store.init_db()
    return data


def test_alias_resolves_to_same_species(db):
    sid = store.upsert_species("玄凤鹦鹉", scientific_name="Nymphicus hollandicus")
    store.add_alias(sid, "玄凤")
    store.add_alias(sid, "鸡尾鹦鹉")
    assert store.resolve_species_name("玄凤") == sid
    assert store.resolve_species_name("鸡尾鹦鹉") == sid
    assert store.resolve_species_name("玄凤鹦鹉") == sid
    assert store.resolve_species_name("不存在的鸟") is None


def test_alias_cannot_point_to_two_species(db):
    a = store.upsert_species("白鹭")
    b = store.upsert_species("大白鹭")
    store.add_alias(a, "小白鹭")
    out = store.add_alias(b, "小白鹭")
    assert out["ok"] is False and out["reason"] == "alias_taken"


def test_reassign_by_alias_does_not_create_duplicate_species(db):
    cockatiel = store.upsert_species("玄凤鹦鹉")
    store.add_alias(cockatiel, "玄凤")
    wrong = store.upsert_species("麻雀")
    oid = store.add_observation(wrong, "2026-11-16", "08:00", None, None,
                                "家", None, None, 0.2, "test", None)
    out = store.reassign_species(oid, "玄凤")
    assert out["new_species"] == "玄凤鹦鹉"
    assert store.resolve_species_name("玄凤") == cockatiel
    detail = store.get_species_detail(cockatiel)
    assert detail["count"] == 1
    assert store.get_species_detail_by_name("麻雀")["count"] == 0


def test_reassign_by_species_id(db):
    target = store.upsert_species("灰斑鸠")
    wrong = store.upsert_species("粉头斑鸠")
    oid = store.add_observation(wrong, "2026-11-16", "08:00", None, None,
                                "测试地", None, None, 0.5, "test", None)
    out = store.reassign_species(oid, species_id=target)
    assert out["new_species"] == "灰斑鸠"
    assert store.get_species_detail(target)["count"] == 1


def test_reassign_unknown_species_id_is_error(db):
    wrong = store.upsert_species("麻雀")
    oid = store.add_observation(wrong, "2026-11-16", "08:00", None, None,
                                "测试地", None, None, 0.5, "test", None)
    assert store.reassign_species(oid, species_id=999999)["error"] == "species_not_found"


def test_search_species_matches_alias_and_canonical(db):
    sid = store.upsert_species("玄凤鹦鹉")
    store.add_alias(sid, "玄凤")
    store.add_observation(sid, "2026-11-16", "08:00", None, None,
                          "家", None, None, 0.9, "test", None)
    hit = store.search_species("玄凤")
    assert hit and hit[0]["common_name_cn"] == "玄凤鹦鹉"
    assert hit[0]["matched_alias"] == "玄凤"
    # 英文名/拉丁名也能搜
    store.upsert_species("虎皮鹦鹉", common_name_en="Budgerigar")
    assert store.search_species("Budger")[0]["common_name_cn"] == "虎皮鹦鹉"


def test_home_defaults_to_shenzhen(db):
    home = locations.get_home()
    assert home["city"] == "深圳"
    assert home["region"] == "CN-44"
    assert abs(home["lat"] - 22.54) < 0.1


def test_set_home_and_unknown_city(db):
    out = locations.set_home("北京")
    assert out["ok"] and out["region"] == "CN-11"
    assert locations.get_home()["city"] == "北京"
    assert locations.get_home()["source"] == "settings"
    bad = locations.set_home("火星")
    assert bad["ok"] is False
    assert locations.get_home()["city"] == "北京", "非法城市不得覆盖"


def test_normalize_city(db):
    assert locations.normalize_city("深圳市") == "深圳"
    assert locations.normalize_city("广东省深圳市南山区") == "深圳"
    assert locations.normalize_city("北京市") == "北京"
    assert locations.normalize_city("未知地方") is None
