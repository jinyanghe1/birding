#!/usr/bin/env python3
"""bird — 本地观鸟数据库命令行。

网页端与 Skill 端共用同一个 SQLite，数据永远一致。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config, identify, pipeline, store


def _setup(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("ultralytics").setLevel(logging.ERROR)
    logging.getLogger("osxphotos").setLevel(logging.ERROR)


# ------------------------------------------------------------------ 命令
def cmd_init(a) -> int:
    store.init_db()
    print(f"数据库已就绪：{config.DB_PATH}")
    return 0


def cmd_stats(a) -> int:
    s = store.get_stats()
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    print(f"鸟种        {s['species']} 种")
    print(f"观测记录    {s['observations']} 次")
    print(f"照片        {s['photos']} 张")
    print(f"地点        {s['places']} 处")
    print(f"待识别      {s['pending_identify']} 张")
    if s["last_scan"]:
        ls = s["last_scan"]
        print(f"上次扫描    {ls['started_at']}  {ls['status']}"
              f"  新增{ls['new_assets']} L1通过{ls['l1_passed']} 保留{ls['l2_kept']}")
    return 0


def cmd_scan(a) -> int:
    stats = pipeline.scan(limit=a.limit, force=a.force, detect=not a.no_detect)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


def cmd_queue(a) -> int:
    if a.take:
        batch = identify.take_batch(a.take)
        if a.prompt:
            print(identify.prompt_for_batch(batch))
        else:
            print(json.dumps(batch, ensure_ascii=False, indent=2))
        return 0
    rows = store.get_queue(a.status, a.limit)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_apply(a) -> int:
    """从 JSON 文件批量回写识别结果。"""
    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [data]
    out = identify.apply_batch(data)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_enrich(a) -> int:
    """补全鸟种元数据：名录匹配 + 科属 + 维基摘要 + 外链。"""
    from . import species_meta
    out = species_meta.enrich(limit=a.limit, use_network=not a.offline,
                              with_links=not a.no_links,
                              with_summary=not a.no_summary)
    print(json.dumps({k: v for k, v in out.items() if k != "not_in_china"},
                     ensure_ascii=False, indent=2))
    if out["not_in_china"]:
        print(f"\n不在中国名录中的物种 {len(out['not_in_china'])} 个"
              f"（加 -v 查看全部；多为低置信误识别）")
    return 0


def cmd_export_site(a) -> int:
    """导出静态 JSON + 缩略图，用于免费静态托管。"""
    from . import export_static
    out = export_static.export(min_conf=a.min_conf, with_thumbs=not a.no_thumbs)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_ebird(a) -> int:
    """eBird 同步：导出 CSV 供手动上传，或提交 checklist。"""
    from . import ebird
    if a.action == "export":
        p = ebird.export_csv(min_conf=a.min_conf)
        print(f"已导出：{p}")
        print(f"\n上传到 eBird：")
        print(f"  1. 打开 https://ebird.org/import/upload.html")
        print(f"  2. 下载官方模板核对列名")
        print(f"  3. 上传 {p.name}")
    elif a.action == "token":
        token = a.token.strip()
        if not token:
            print("请提供 token：bird ebird token <your-token>")
            return 1
        from pathlib import Path
        Path(config.DATA_DIR / ".ebird_token").write_text(token)
        print("token 已保存")
    elif a.action == "test":
        r = ebird._req("https://api.ebird.org/v2/ref/region/list/country/world")
        print("token 有效" if r else "token 无效或未设置")
    elif a.action == "auto-submit":
        from . import ebird_submit
        return ebird_submit.auto_submit()
    return 0


def cmd_merge(a) -> int:
    """合并同物种+同日+同地点簇的被拆散观测。"""
    from . import maintenance
    out = maintenance.merge_observations(dry_run=a.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if a.infer_places:
        out2 = maintenance.infer_places(dry_run=a.dry_run)
        print(json.dumps(out2, ensure_ascii=False, indent=2))
    return 0


def cmd_auto(a) -> int:
    """本地模型自动识别队列里的照片。"""
    out = identify.auto_identify(limit=a.limit, accept_conf=a.accept)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_list(a) -> int:
    rows = store.get_species_list(order=a.order, limit=a.limit, search=a.search)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("还没有任何观测记录。先跑 `bird scan` 再识别。")
        return 0
    print(f"{'中文名':<16}{'次数':>4}  {'首次':<12}{'最近':<12}  主要地点")
    print("-" * 92)
    for r in rows:
        pl = (r.get("top_place") or "").replace(", 中国", "")
        print(f"{r['common_name_cn'][:15]:<16}{r['cnt']:>4}  "
              f"{r['first_date'] or '':<12}{r['last_date'] or '':<12}  {pl[:34]}")
    return 0


def cmd_show(a) -> int:
    d = store.get_species_detail_by_name(a.name)
    if not d:
        # 退化为搜索
        rows = store.get_species_list(search=a.name, limit=10)
        if not rows:
            print(f"没找到「{a.name}」")
            return 1
        print("未精确匹配，相似结果：")
        for r in rows:
            print(f"  {r['common_name_cn']}  {r['cnt']} 次")
        return 0
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    sp = d["species"]
    print(f"{sp['common_name_cn']}  {sp.get('scientific_name') or ''}")
    if sp.get("family_cn"):
        print(f"  分类：{sp.get('order_cn') or ''} / {sp['family_cn']}")
    print(f"  观测 {d['count']} 次")
    if d["places"]:
        print(f"  地点：{', '.join(d['places'][:8])}")
    print("\n  记录：")
    for o in d["observations"]:
        print(f"   {o['obs_date']}  {o.get('obs_time') or '':<9}"
              f"  {o.get('place_name') or '（无地点）'}")
        if o.get("notes"):
            print(f"        {o['notes']}")
        for p in o["photos"][:3]:
            print(f"        - {p.get('image_path')}")
    return 0


def cmd_places(a) -> int:
    rows = store.get_places(a.limit)
    for r in rows:
        print(f"{r['c']:>4} 次 / {r['sp']:>3} 种   {r['place_name']}")
    return 0


def cmd_review(a) -> int:
    rows = store.get_low_confidence(a.threshold)
    if not rows:
        print("没有需要复核的记录。")
        return 0
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(a) -> int:
    import uvicorn
    from .web import app
    print(f"观鸟数据库：http://{config.WEB_HOST}:{a.port}")
    uvicorn.run(app, host=config.WEB_HOST, port=a.port, log_level="warning")
    return 0


def cmd_probe(a) -> int:
    """只读探针：打印照片库画像，不做任何写入。"""
    from . import photos as ph
    assets = list(ph.iter_assets())
    n = len(assets)
    local = sum(1 for x in assets if x.image_source == "original")
    deriv = sum(1 for x in assets if x.image_source == "derivative")
    none = sum(1 for x in assets if x.image_path is None)
    gps = sum(1 for x in assets if x.latitude is not None)
    place = sum(1 for x in assets if x.place_name)
    movie = sum(1 for x in assets if x.ismovie)
    skip = {}
    for x in assets:
        r = ph.should_skip(x)
        if r:
            skip[r] = skip.get(r, 0) + 1
    out = {
        "总数": n, "本地原图": local, "缩略图": deriv, "无图": none,
        "有GPS": gps, "有中文地名": place, "视频": movie, "将被跳过": skip,
        "待检测": n - sum(skip.values()),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# ------------------------------------------------------------------ 入口
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bird", description="本地观鸟数据库")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="初始化数据库").set_defaults(fn=cmd_init)

    s = sub.add_parser("stats", help="统计概览")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("probe", help="只读探针：照片库画像")
    s.set_defaults(fn=cmd_probe)

    s = sub.add_parser("scan", help="扫描照片库（L0+L1+L2）")
    s.add_argument("--limit", type=int, help="只处理前 N 张")
    s.add_argument("--force", action="store_true", help="忽略水位线，全量重扫")
    s.add_argument("--no-detect", action="store_true", help="跳过 L1 检测（调试用）")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("queue", help="识别队列")
    s.add_argument("--take", type=int, help="取出 N 条待识别")
    s.add_argument("--prompt", action="store_true", help="输出给 Agent 的提示词")
    s.add_argument("--status", default="pending")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_queue)

    s = sub.add_parser("apply", help="回写识别结果（JSON 文件）")
    s.add_argument("file")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("auto", help="本地模型自动识别队列")
    s.add_argument("--limit", type=int, help="只处理前 N 条")
    s.add_argument("--accept", type=float, default=0.45,
                   help="置信度高于此值视为可接受，低于则进入复核")
    s.set_defaults(fn=cmd_auto)

    s = sub.add_parser("list", help="鸟种列表")
    s.add_argument("--order", default="count", choices=["count", "recent", "name"])
    s.add_argument("--search", default="")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="查看某个鸟种")
    s.add_argument("name")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("places", help="地点排行")
    s.add_argument("--limit", type=int, default=15)
    s.set_defaults(fn=cmd_places)

    s = sub.add_parser("review", help="低置信度复核")
    s.add_argument("--threshold", type=float, default=config.MIN_AGENT_CONF)
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("enrich", help="补全鸟种元数据（名录/科属/摘要/外链）")
    s.add_argument("--limit", type=int, help="只处理前 N 个物种")
    s.add_argument("--offline", action="store_true", help="只用本地名录，不联网")
    s.add_argument("--no-links", action="store_true", help="不生成外链")
    s.add_argument("--no-summary", action="store_true", help="不抓维基摘要")
    s.set_defaults(fn=cmd_enrich)

    s = sub.add_parser("export-site", help="导出静态 JSON + 缩略图（GitHub Pages）")
    s.add_argument("--min-conf", type=float, default=config.UI_MIN_CONF)
    s.add_argument("--no-thumbs", action="store_true")
    s.set_defaults(fn=cmd_export_site)

    s = sub.add_parser("ebird", help="eBird 同步")
    s.add_argument("action", choices=["export", "token", "test", "auto-submit"])
    s.add_argument("--token", default="", help="API token")
    s.add_argument("--min-conf", type=float, default=0.45)
    s.set_defaults(fn=cmd_ebird)

    s = sub.add_parser("merge-obs", help="合并被拆散的观测记录")
    s.add_argument("--dry-run", action="store_true", help="只统计不执行")
    s.add_argument("--infer-places", action="store_true",
                   help="顺带做地点时间邻近推断")
    s.set_defaults(fn=cmd_merge)

    s = sub.add_parser("serve", help="启动网页")
    s.add_argument("--port", type=int, default=config.WEB_PORT)
    s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    _setup(a.verbose)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
