#!/usr/bin/env python
"""批量复核工具：导出待复核批次 → 人工/AI 看图 → 回写结论。

背景：本地分类模型置信度不可靠（实测 0.11 是真鸟、0.62 是人民大会堂），
不能按阈值批量删数据，必须逐张看图。本脚本只做「导出批次 + 回写结论」，
看图与判断由人或多模态模型完成。

用法：
  # 1. 导出批次（每个物种取一张代表图，便于判断物种级结论）
  python scripts/review_batch.py export --bucket high_conf_foreign --out /tmp/batch.json

  # 2. 看图后写结论 decisions.json：
  #    [{"obs_id": 11, "decision": "not_bird"},
  #     {"obs_id": 24, "decision": "reassign", "species": "黑领椋鸟"},
  #     {"obs_id": 31, "decision": "keep"}]
  # 3. 回写
  python scripts/review_batch.py apply --file decisions.json [--dry-run]

bucket 取值：
  high_conf_foreign  置信≥0.45 但不在中国名录（最误导，优先级最高）
  mid                0.30–0.45 区间（模型自己也不确定）
  low                置信<0.20（噪声最多，但夹着真鸟，量大）
  all_foreign        所有不在中国名录的
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birdscan import store  # noqa: E402

BUCKETS = {
    # name: (WHERE 片段, 排序)
    "high_conf_foreign": ("o.confidence >= 0.45 AND s.in_china = 0",
                          "o.confidence DESC"),
    "mid": ("o.confidence >= 0.30 AND o.confidence < 0.45",
            "o.confidence DESC"),
    "low": ("o.confidence < 0.20", "o.confidence ASC"),
    "all_foreign": ("s.in_china = 0", "o.confidence DESC"),
}


def _rows(where: str, order: str, group_by_species: bool) -> list[dict]:
    """取待复核样本，附带代表图路径与同物种待复核条数。"""
    group = "GROUP BY s.id" if group_by_species else ""
    sql = f"""
      SELECT o.id obs_id, o.obs_date, o.place_name, o.confidence,
             s.id species_id, s.common_name_cn, s.common_name_en,
             s.in_china, s.family_cn,
             (SELECT COUNT(*) FROM observations o2
                WHERE o2.species_id = s.id AND o2.confidence < 0.45) same_species_pending,
             (SELECT p.thumb_cache FROM photos p WHERE p.obs_id = o.id
                AND p.thumb_cache IS NOT NULL LIMIT 1) image,
             (SELECT p2.image_path FROM photos p2 WHERE p2.obs_id = o.id
                ORDER BY p2.is_representative DESC, p2.sharpness DESC LIMIT 1) image_orig
      FROM observations o JOIN species s ON s.id = o.species_id
      WHERE {where}
      {group}
      ORDER BY {order}
    """
    with store.conn_ctx(readonly=True) as con:
        return [dict(r) for r in con.execute(sql)]


def cmd_export(a) -> int:
    where, order = BUCKETS[a.bucket]
    rows = _rows(where, order, not a.all_obs)
    if a.limit:
        rows = rows[:a.limit]
    # 只留存在的图
    for r in rows:
        p = r.get("image") or r.get("image_orig")
        r["image_exists"] = bool(p and Path(p).exists())
    out = Path(a.out)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    missing = sum(1 for r in rows if not r["image_exists"])
    print(f"导出 {len(rows)} 条 → {out}")
    print(f"缺图 {missing} 条")
    print("\n示例决策文件格式：")
    print(json.dumps([
        {"obs_id": rows[0]["obs_id"], "decision": "not_bird"} if rows else {},
        {"obs_id": rows[-1]["obs_id"] if rows else 0,
         "decision": "reassign", "species": "白鹭"},
    ], ensure_ascii=False, indent=2))
    return 0


def cmd_apply(a) -> int:
    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [data]
    stats = {"not_bird": 0, "reassign": 0, "keep": 0, "annotated": 0,
             "skipped": 0}
    for item in data:
        oid = item.get("obs_id")
        act = item.get("decision")
        if not oid or act not in stats:
            stats["skipped"] += 1
            continue
        if act == "keep":
            # keep 可带 note：把复核结论写进 notes（笼养/存疑等），不改物种
            note = item.get("note")
            if note and not a.dry_run:
                store.annotate_observation(oid, note)
                stats["annotated"] += 1
            stats["keep"] += 1
            continue
        if a.dry_run:
            print(f"[dry-run] obs {oid}: {act} "
                  f"{item.get('species', '')}")
            stats[act] += 1
            continue
        try:
            if act == "not_bird":
                store.mark_not_bird(oid)
            else:
                store.reassign_species(oid, item["species"])
            stats[act] += 1
        except Exception as e:  # 单条失败不打断整批
            print(f"!! obs {oid} {act} 失败: {e}")
            stats["skipped"] += 1
    print(json.dumps({"dry_run": a.dry_run, **stats}, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="批量复核：导出批次 / 回写结论")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="导出待复核批次")
    e.add_argument("--bucket", choices=list(BUCKETS), default="high_conf_foreign")
    e.add_argument("--limit", type=int, default=0, help="限制条数（0=不限）")
    e.add_argument("--all-obs", action="store_true",
                   help="按观测导出（默认按物种去重，每物种一条代表）")
    e.add_argument("--out", default="/tmp/review_batch.json")
    e.set_defaults(fn=cmd_export)

    ap = sub.add_parser("apply", help="回写复核结论")
    ap.add_argument("--file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-sync", action="store_true", help="保留参数（同步由流水线负责）")
    ap.set_defaults(fn=cmd_apply)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
