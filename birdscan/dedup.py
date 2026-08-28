"""L2 连拍分组 + 感知哈希去重 + 清晰度选片。

实测：Photos 自带的 burst 标记在本机只有 3/15,413 张，等于没有，
所以连拍分组必须靠「时间戳间隔 + 感知哈希」自建。

顺序很关键：先分组去重，再比清晰度。
否则整组都糊时会把整组全删光，一张不留。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config

try:
    import cv2
    import numpy as np
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import imagehash
    from PIL import Image
    _IH = True
except ImportError:
    _IH = False


@dataclass
class Candidate:
    uuid: str
    path: str
    shot_at: str | None
    timestamp: float          # 用于分组的排序键
    latitude: float | None
    longitude: float | None
    place_name: str | None
    sharpness: float = 0.0
    phash: str | None = None
    animal_conf: float = 0.0
    width: int = 0
    height: int = 0
    burst_group: str = ""
    media_type: str = "image"
    video_offset_sec: float | None = None


# ------------------------------------------------------------------ 评分
def sharpness_of(path: str) -> float:
    """Laplacian 方差。<50 明显糊 | 50-150 borderline | >200 清晰。"""
    try:
        if _CV2:
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                return 0.0
            return float(cv2.Laplacian(img, cv2.CV_64F).var())
        from PIL import Image, ImageFilter, ImageStat
        im = Image.open(path).convert("L")
        edges = im.filter(ImageFilter.FIND_EDGES)
        return float(ImageStat.Stat(edges).stddev[0] ** 2)
    except Exception:
        return 0.0


def phash_of(path: str) -> str | None:
    """64 位感知哈希，返回 16 位 hex。用 pHash 而非 dHash：DCT 低频更鲁棒。"""
    if not _IH:
        return None
    try:
        with Image.open(path) as im:
            return str(imagehash.phash(im.convert("RGB"), hash_size=config.PHASH_SIZE))
    except Exception:
        return None


def _hamming(h1: str | None, h2: str | None) -> int:
    if not h1 or not h2 or len(h1) != len(h2):
        return 99
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def exposure_score(path: str) -> float:
    """直方图两端裁剪越少越好，返回 0-1。"""
    try:
        if _CV2:
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                return 0.5
            hist = cv2.calcHist([img], [0], None, [256], [0, 256]).flatten()
        else:
            from PIL import Image
            hist = Image.open(path).convert("L").histogram()
            hist = np.array(hist) if _CV2 else hist
        total = sum(hist) or 1
        clipped = (hist[:5].sum() + hist[-5:].sum()) / total if _CV2 else \
            (sum(hist[:5]) + sum(hist[-5:])) / total
        return max(0.0, 1.0 - clipped * 5)
    except Exception:
        return 0.5


# ------------------------------------------------------------------ 分组
def group_bursts(cands: list[Candidate]) -> list[list[Candidate]]:
    """按拍摄时间戳排序，相邻间隔 <= BURST_GAP_SEC 归为一组。"""
    ordered = sorted(cands, key=lambda c: c.timestamp)
    groups: list[list[Candidate]] = []
    cur: list[Candidate] = []
    for c in ordered:
        if cur and (c.timestamp - cur[-1].timestamp) <= config.BURST_GAP_SEC:
            cur.append(c)
        else:
            if cur:
                groups.append(cur)
            cur = [c]
    if cur:
        groups.append(cur)
    return groups


def dedup_group(group: list[Candidate]) -> list[Candidate]:
    """组内两两算汉明距离，<=6 视为同一张，每簇保留清晰度最高的一张。"""
    kept: list[Candidate] = []
    for c in sorted(group, key=lambda x: -x.sharpness):
        if all(_hamming(c.phash, k.phash) > config.PHASH_DUP_DIST for k in kept):
            kept.append(c)
    return kept


def score_candidates(group: list[Candidate]) -> None:
    """组内综合打分，用于挑代表图。"""
    n = len(group)
    sharps = [c.sharpness for c in group]
    smin, smax = min(sharps), max(sharps)
    span = (smax - smin) or 1.0
    px = [c.width * c.height for c in group]
    pmax = max(px) or 1
    for i, c in enumerate(group):
        norm_sharp = (c.sharpness - smin) / span
        norm_px = (c.width * c.height) / pmax
        # 连拍中间帧优先：两端常有抖动
        pos = 1.0 - abs(i - (n - 1) / 2) / max(1, (n - 1) / 2 or 1)
        c._score = (0.30 * norm_sharp + 0.20 * norm_px
                    + 0.20 * c.animal_conf
                    + 0.15 * pos
                    + 0.15 * exposure_score(c.path))
    group.sort(key=lambda c: -getattr(c, "_score", 0.0))


def select(groups: list[list[Candidate]]) -> list[Candidate]:
    """每个连拍组 -> 去重 -> 打分 -> 保留前 MAX_KEEP_PER_BURST 张。"""
    out: list[Candidate] = []
    for gi, g in enumerate(groups):
        deduped = dedup_group(g)
        score_candidates(deduped)
        for rank, c in enumerate(deduped[: config.MAX_KEEP_PER_BURST]):
            c.burst_group = f"g{gi}"
            c._rank = rank
            out.append(c)
    return out
