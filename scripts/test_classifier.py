"""临时测试：验证「YOLO 框鸟 -> 裁剪 -> ConvNeXt 分类」的效果与速度。"""
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.disable(logging.WARNING)

from birdscan.classifier import identify_file  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "birds.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
rows = con.execute(
    "SELECT asset_uuid, image_path, animal_conf FROM id_queue "
    "ORDER BY animal_conf DESC LIMIT ?", (n,)
).fetchall()

hit = 0
t0 = time.time()
for r in rows:
    res = identify_file(r["image_path"], topk=2)
    if res["is_bird"]:
        hit += 1
        c = res["candidates"][0]
        tag = "OK " if c["confidence"] >= 0.5 else "弱 "
        print(f"{tag}框{res['box_conf']:.2f} -> {c['common_name_cn']:<20}"
              f"{c['confidence']*100:5.1f}%")
    else:
        print("   未检出鸟")
dt = time.time() - t0
print(f"\n{hit}/{len(rows)} 检出鸟 | 总耗时 {dt:.1f}s -> {dt/max(1,len(rows))*1000:.0f} ms/张")
