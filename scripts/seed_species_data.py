#!/usr/bin/env python
"""物种数据种子：别名归一 + 中文名修正 + 常住地默认。

幂等，可重复执行。解决两个用户痛点：
1. 同鸟多名（玄凤 / 玄凤鹦鹉 / 鸡尾鹦鹉）被存成多个物种
   → 正名唯一 + 别名表归一，修正时输入任意叫法都落到同一物种。
2. 部分物种中文名字段里是英文（早期名录缺中文）
   → 用人工校订的中文名回写（比机翻可靠，鸟名有固定译名）。

用法：python scripts/seed_species_data.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birdscan import locations, store  # noqa: E402

# (正名, 英文名, 拉丁学名, [别名...])
SEED_SPECIES = [
    ("玄凤鹦鹉", "Cockatiel", "Nymphicus hollandicus", ["玄凤", "鸡尾鹦鹉"]),
    ("虎皮鹦鹉", "Budgerigar", "Melopsittacus undulatus", ["虎皮", "娇凤"]),
    ("小葵花凤头鹦鹉", "Yellow-crested Cockatoo", "Cacatua sulphurea",
     ["小葵花鹦鹉"]),
    ("灰斑鸠", "Eurasian Collared Dove", "Streptopelia decaocto", ["灰鸠"]),
    ("珠颈斑鸠", "Spotted Dove", "Spilopelia chinensis", ["斑鸠", "珠颈鸽"]),
    ("白鹭", "Little Egret", "Egretta garzetta", ["小白鹭"]),
    ("黑翅长脚鹬", "Black-winged Stilt", "Himantopus himantopus", ["高跷鸻"]),
]

# 中文名修正：字段里塞了英文名的物种（2026-11-16 实测共 7 条）
CN_FIX = {
    "Bonapartes Gull": ("博氏鸥", "Chroicocephalus philadelphia"),
    "Bolles Pigeon": ("波氏鸽", "Columba bollii"),
    "Puerto Rican Parrot": ("波多黎各鹦鹉", "Amazona vittata"),
    "Striated Swallow": ("纹腹燕", "Cecropis striolata"),
    "Cassin's Honeyguide": ("卡氏响蜜鴷", "Prodotiscus insignis"),
    "Chatham Island Pigeon": ("查塔姆岛鸠", "Hemiphaga chathamensis"),
    "Japanese Tit": ("远东山雀", "Parus minor"),
}


def seed_species() -> None:
    for cn, en, sci, aliases in SEED_SPECIES:
        sid = store.resolve_species_name(cn)
        if not sid:
            # 正名不在库里，但别名可能是现有物种 → 把那条改正名，旧名变别名
            for al in aliases:
                sid = store.resolve_species_name(al)
                if sid:
                    detail = store.get_species_detail(sid)["species"]
                    store.update_species(sid, {"common_name_cn": cn,
                                               "common_name_en": en,
                                               "scientific_name": sci})
                    store.add_alias(sid, detail["common_name_cn"], source="seed")
                    print(f"  正名归一: {detail['common_name_cn']} → {cn}")
                    break
        if not sid:
            sid = store.upsert_species(cn, scientific_name=sci, common_name_en=en)
            print(f"  新建物种: {cn}")
        for al in aliases:
            store.add_alias(sid, al, source="seed")
        print(f"  {cn}: 别名 {store.get_aliases(sid)}")


def fix_chinese_names() -> None:
    # 注意：不要在写事务里再调 store.add_alias（嵌套写连接会 SQLITE_BUSY）
    changed: list[tuple[int, str]] = []
    with store.conn_ctx() as con:
        for en, (cn, sci) in CN_FIX.items():
            row = con.execute(
                "SELECT id, common_name_cn FROM species WHERE common_name_cn = ?",
                (en,)).fetchone()
            if not row:
                continue
            clash = con.execute(
                "SELECT id FROM species WHERE common_name_cn = ? AND id != ?",
                (cn, row["id"])).fetchone()
            if clash:
                print(f"  跳过 {en} → {cn}（中文名已存在）")
                continue
            con.execute(
                "UPDATE species SET common_name_cn = ?, scientific_name = ?"
                " WHERE id = ?", (cn, sci, row["id"]))
            changed.append((row["id"], en))
            print(f"  中文名修正: {en} → {cn}")
    for sid, en in changed:
        store.add_alias(sid, en, source="seed")


def main() -> int:
    store.init_db()
    print("== 别名与正名归一 ==")
    seed_species()
    print("== 中文名修正 ==")
    fix_chinese_names()
    print("== 常住地 ==")
    home = locations.get_home()
    print(f"  当前常住地: {home['city']}（来源 {home['source']}）")
    if home["source"] == "default":
        print(f"  写入默认常住地: {locations.set_home('深圳')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
