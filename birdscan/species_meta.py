"""鸟种元数据补全 + 外链生成。

数据源（全部匿名、无 key，均已实测可用）：
  主源  中国鸟类名录 v10.0  data/china_bird_list_v10.csv
        来自 github.com/makenv/bird-list-en，1,616 种
        字段：序号, 学名, 中文名, 英文名, IUCN等级, 国家保护级别
        科/目由分隔行（如 `1,ANSERIFORMES,鸭科,Anatidae (24:60),,,`）向下填充
  GBIF  api.gbif.org/v1/species/search?q=<拉丁学名>&rank=SPECIES&limit=1  -> 科/目
  iNat  api.inaturalist.org/v1/taxa?q=<拉丁学名>&rank=species&locale=zh-CN
        -> preferred_common_name / wikipedia_url / id / conservation_status
        ⚠️ 必须用拉丁学名查；用中文名查会命中错的物种（实测「夜鹭」返回大蓝鹭）
  维基  zh.wikipedia.org/api/rest_v1/page/summary/<中文名>  -> 中文摘要

外链（实测 200）：
  eBird            https://ebird.org/species/<species-code>
  Birds of the World  https://birdsoftheworld.org/bow/species/<species-code>
  iNaturalist      https://www.inaturalist.org/taxa/<id>
  中文维基          https://zh.wikipedia.org/wiki/<中文名>
⚠️ eBird species code 无匿名映射源，按英文名生成候选后需逐个 HTTP 校验，
  校验失败则不给 eBird/BOW 链接（宁缺毋滥）。
"""
from __future__ import annotations

import csv
import json
import logging
import re
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, store

log = logging.getLogger("birdscan")

CSV_PATH = Path(config.DATA_DIR) / "china_bird_list_v10.csv"
CACHE_PATH = Path(config.DATA_DIR) / "meta_cache.json"
TIMEOUT = 12

# birds.cornell.edu 系站点会拦掉自定义 UA（实测返回非 200），必须带浏览器 UA。
# 用途仅为低频（每个物种 1 次）只读公开物种页，不做爬取。
_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36")}


def _get(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        log.debug("请求失败 %s: %s", url, e)
        return None


def _head_ok(url: str) -> bool:
    """用 GET 而非 HEAD：实测 birdsoftheworld.org 不支持 HEAD（HEAD 一律失败），
    而 curl -L GET 正常。这里只读前 2KB 就关掉连接，开销可忽略。"""
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            if r.status != 200:
                return False
            r.read(2048)
            return True
    except Exception as e:
        log.debug("校验失败 %s: %s", url, e)
        return False


# ------------------------------------------------------------------ 名录
def load_china_checklist() -> dict:
    """返回 {"by_cn": {...}, "by_sci": {...}, "by_en": {...}}"""
    if not CSV_PATH.exists():
        log.warning("名录 CSV 不存在：%s", CSV_PATH)
        return {"by_cn": {}, "by_sci": {}, "by_en": {}}
    by_cn, by_sci, by_en = {}, {}, {}
    order_latin = family_cn = family_latin = ""
    with CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 4:
                continue
            c1, c2, c3, c4 = (x.strip() for x in row[:4])
            # 分隔行：第 2 列全大写拉丁目名，第 3 列是中文科名
            if c2.isupper() and not re.search(r"[a-z]", c2) and " " not in c2.strip("()"):
                order_latin = c2.title()
                family_cn = c3
                family_latin = re.sub(r"\s*\(.*\)", "", c4).strip()
                continue
            sci, cn, en = c2, c3, c4
            if not sci or " " not in sci:
                continue
            iucn = row[4].strip() if len(row) > 4 else ""
            prot = row[5].strip() if len(row) > 5 else ""
            rec = {
                "scientific_name": sci, "common_name_cn": cn, "common_name_en": en,
                "order_cn": "", "order_latin": order_latin,
                "family_cn": family_cn, "family_latin": family_latin,
                "iucn_status": iucn, "china_protection": prot if prot not in ("-", "") else None,
                "in_china": 1,
            }
            by_cn[cn] = rec
            by_sci[sci.lower()] = rec
            if en:
                by_en[en.lower()] = rec
    log.info("名录载入：%d 种", len(by_cn))
    return {"by_cn": by_cn, "by_sci": by_sci, "by_en": by_en}


# ------------------------------------------------------------------ 网络补全
def gbif_taxonomy(sci: str) -> dict:
    d = _get("https://api.gbif.org/v1/species/search?q="
             + urllib.parse.quote(sci) + "&rank=SPECIES&limit=1")
    r = (d or {}).get("results") or []
    if not r:
        return {}
    r0 = r[0]
    return {"family_latin": r0.get("family"), "order_latin": r0.get("order"),
            "class_name": r0.get("class"), "gbif_key": r0.get("key"),
            "canonical": r0.get("canonicalName")}


def inat_taxa(sci: str) -> dict:
    d = _get("https://api.inaturalist.org/v1/taxa?q=" + urllib.parse.quote(sci)
             + "&rank=species&locale=zh-CN&per_page=1")
    r = (d or {}).get("results") or []
    if not r:
        return {}
    r0 = r[0]
    cs = r0.get("conservation_status") or {}
    return {"inat_id": r0.get("id"),
            "common_name_cn": r0.get("preferred_common_name"),
            "wikipedia_url": r0.get("wikipedia_url"),
            "iucn": (cs.get("status") or "").upper() or None,
            "iconic": r0.get("iconic_taxon_name")}


def wiki_summary(cn: str) -> str | None:
    d = _get("https://zh.wikipedia.org/api/rest_v1/page/summary/"
             + urllib.parse.quote(cn))
    return (d or {}).get("extract") or None


def ebird_code(en_name: str) -> str | None:
    """由英文名猜 eBird species code 并逐个校验。猜不中就返回 None（宁缺毋滥）。

    eBird code 没有匿名映射源（官方 CSV 被 Cloudflare 拦），只能用启发式生成候选
    再校验。已验证可行的规则：
      * 两个词 3+3              Little Egret -> litegr, Mute Swan -> mutswa
      * 连字符名取各部分首字母 + 末部分前 3 位
                               Black-crowned Night-Heron -> bcnher
      * 三个词 2+2+2
      * 单词取前 6 位
    重名冲突时 eBird 会加数字后缀（Cackling Goose -> cacgoo1），逐个试。

    ⚠️ 校验必须用 birdsoftheworld.org，不能用 ebird.org：
    实测 eBird 现在**所有物种页都 302 → 登录页**（即使 code 正确），无法校验；
    BOW 用同一套 code 且无需登录，有效 200 / 无效 404（zzzzzz 实测 404）。
    """
    if not en_name:
        return None
    words = re.findall(r"[A-Za-z]+", en_name)
    parts = re.split(r"[\s\-]+", en_name.strip())
    parts = [p for p in parts if re.search(r"[A-Za-z]", p)]
    cands: list[str] = []
    if len(words) == 2:
        cands.append(words[0][:3] + words[1][:3])
    if len(parts) >= 3:
        cands.append("".join(p[0] for p in parts[:-1]) + parts[-1][:3])
    if len(words) >= 3:
        cands.append("".join(w[:2] for w in words[:3]))
    if len(words) == 1:
        cands.append(words[0][:6])
    seen, out = set(), []
    for c in cands:
        c = c.lower()
        if c not in seen:
            seen.add(c)
            out.append(c)
    for cand in out:
        for tail in ("", "1", "2", "3"):
            code = cand + tail
            if len(code) < 5:
                continue
            if _head_ok(f"https://birdsoftheworld.org/bow/species/{code}"):
                return code
    return None


# ------------------------------------------------------------------ 主流程
def enrich(limit: int = 200, use_network: bool = True,
           with_links: bool = True, with_summary: bool = True,
           min_obs_conf: float = 0.30) -> dict:
    """补全 species 表。返回统计。

    min_obs_conf：只有最高观测置信度 >= 此值的物种才走网络请求。
    低置信的（大多是中国没有的误识别）只做本地名录匹配，避免几百次无谓的
    HTTP 请求。
    """
    store.init_db()
    ck = load_china_checklist()
    rows = store.get_all_species()
    if limit:
        rows = rows[:limit]
    # 按「是否值得联网」排序：有意义的排前面
    with store.conn_ctx(readonly=True) as con:
        best = {r["species_id"]: (r["mc"] or 0)
                for r in con.execute("SELECT species_id, MAX(confidence) mc "
                                     "FROM observations GROUP BY species_id")}
    rows.sort(key=lambda s: -(best.get(s["id"], 0)))
    out = {"total": len(rows), "matched_checklist": 0, "gbif": 0,
           "inat": 0, "wiki": 0, "links": 0, "not_in_china": [],
           "network_skipped": 0}

    for i, sp in enumerate(rows):
        relevant = best.get(sp["id"], 0) >= min_obs_conf
        cn = sp["common_name_cn"]
        rec = None
        if cn in ck["by_cn"]:
            rec = ck["by_cn"][cn]
        elif sp.get("common_name_en") and sp["common_name_en"].lower() in ck["by_en"]:
            rec = ck["by_en"][sp["common_name_en"].lower()]
        elif sp.get("scientific_name") and sp["scientific_name"].lower() in ck["by_sci"]:
            rec = ck["by_sci"][sp["scientific_name"].lower()]

        patch: dict = {}
        if rec:
            out["matched_checklist"] += 1
            patch.update({k: v for k, v in rec.items()
                          if k in ("scientific_name", "common_name_en", "family_cn",
                                   "order_cn", "iucn_status", "china_protection",
                                   "in_china") and v})
            patch.setdefault("in_china", 1)
        else:
            out["not_in_china"].append(cn)
            patch["in_china"] = 0

        if use_network and not sp.get("family_cn") and not relevant:
            out["network_skipped"] += 1
        if use_network and not sp.get("family_cn") and relevant:
            sci = rec.get("scientific_name") if rec else sp.get("scientific_name")
            if sci:
                g = gbif_taxonomy(sci)
                if g.get("order_latin") or g.get("family_latin"):
                    out["gbif"] += 1
                    patch.setdefault("family_latin", g.get("family_latin"))
                    patch.setdefault("order_latin", g.get("order_latin"))
            n = inat_taxa(sci) if sci else {}
            if n:
                out["inat"] += 1
                if n.get("inat_id"):
                    patch.setdefault("inat_id", n["inat_id"])
                if n.get("wikipedia_url"):
                    patch.setdefault("wikipedia_url", n["wikipedia_url"])
                if n.get("iucn") and not patch.get("iucn_status"):
                    patch["iucn_status"] = n["iucn"]

        if with_summary and not sp.get("summary") and relevant:
            s = wiki_summary(cn)
            if s:
                out["wiki"] += 1
                patch["summary"] = s[:900]

        if with_links and relevant:
            links = build_links(cn, patch.get("scientific_name") or sp.get("scientific_name"),
                                patch.get("common_name_en") or sp.get("common_name_en"),
                                patch.get("inat_id") or sp.get("inat_id"))
            if links:
                out["links"] += 1
                patch["links_json"] = json.dumps(links, ensure_ascii=False)

        if patch:
            store.update_species(sp["id"], patch)
        if (i + 1) % 25 == 0:
            log.info("  元数据进度 %d/%d", i + 1, len(rows))
    return out


def build_links(cn: str, sci: str | None, en: str | None,
                inat_id: int | None, verify_ebird: bool = True) -> dict:
    """双轨外链：能校验出 species code 就给直接页，否则给搜索页保底。

    实测可用性（2026-08）：
      维基百科      200   eBird 搜索   200   Xeno-canto 搜索  200
      eBird 物种页  302(需登录，浏览器内已登录则正常)
      BOW 物种页    200   iNat 物种页  403(curl) 但浏览器正常
      懂鸟 dongniao.net / 中国观鸟记录中心 —— 均无可用 URL 格式，不给链接
    """
    links = {}
    if cn:
        links["维基百科"] = "https://zh.wikipedia.org/wiki/" + urllib.parse.quote(cn)
    if inat_id:
        links["iNaturalist"] = f"https://www.inaturalist.org/taxa/{inat_id}"
    code = ebird_code(en) if (verify_ebird and en) else None
    if code:
        links["eBird"] = f"https://ebird.org/species/{code}"
        links["Cornell 世界鸟类"] = f"https://birdsoftheworld.org/bow/species/{code}"
    if sci:
        links["eBird 搜索"] = ("https://ebird.org/search?query="
                               + urllib.parse.quote(sci))
        links["Xeno-canto 鸟鸣"] = ("https://xeno-canto.org/explore?query="
                                    + urllib.parse.quote(f'sp:"{sci}"'))
        links["GBIF"] = ("https://www.gbif.org/species/search?q="
                         + urllib.parse.quote(sci))
    return links
