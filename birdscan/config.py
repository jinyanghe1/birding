"""集中配置：所有阈值与路径都在这里，调参只改这一个文件。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "birds.db"
THUMB_DIR = DATA_DIR / "thumbs"
FRAME_DIR = DATA_DIR / "frames"
CHECKLIST_CSV = DATA_DIR / "china_bird_list_v10.csv"
MODEL_DIR = ROOT / "models"
LOG_DIR = ROOT / "logs"

for _d in (DB_PATH.parent, THUMB_DIR, FRAME_DIR, MODEL_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- L0 读取层
LIBRARY_PATH = None                 # None = 使用系统默认照片库
# 实测会有 1,831 张无 GPS 的 PNG 命中"疑似截图"规则，但其中可能混有导出的真实鸟照。
# 多检测它们只多花约 80 秒，误杀却不可恢复（要全量重扫）。L0 优先保召回，交给 L1 去筛。
SKIP_PNG_SCREENSHOT = False
MIN_PIXELS = 200_000                # 小于 20 万像素跳过

# ---------------------------------------------------------------- L1 检测层
# MegaDetector V6 权重（本地 models/ 目录）
#   MDV6-yolov10-c  5.5MB  2.3M 参数   mps@960 ≈ 44ms/张   （默认）
#   MDV6-yolov9-c   100MB  25.5M 参数  精度更好但更慢
MD_WEIGHTS = os.environ.get("BIRDSCAN_MD_WEIGHTS", "MDV6-yolov10-c.pt")
MD_DEVICE = os.environ.get("BIRDSCAN_MD_DEVICE", "mps")   # mps | cpu
DETECT_CONF = 0.20                  # 动物类别放行阈值
DETECT_INPUT_SIZE = 960             # 实测 960/mps ≈ 44ms/张，全库约 11 分钟

# 备用检测器：Apple Vision（pyobjc），MegaDetector 不可用时自动降级
VISION_BIRD_LABELS = {
    "bird", "heron", "owl", "gull", "hummingbird", "ostrich", "cockatoo",
    "flamingo", "penguin", "swan", "duck", "eagle", "falcon", "hawk",
    "parrot", "peacock", "pigeon", "sparrow", "robin", "woodpecker",
}
VISION_CONF = 0.10                  # Vision 置信度很低，阈值放宽松

# ---------------------------------------------------------------- L2 去重层
BURST_GAP_SEC = 2.0                 # 相邻两张时间差 <=2s 视为同一连拍组
PHASH_SIZE = 8                      # 64 位感知哈希
PHASH_DUP_DIST = 6                  # 汉明距离 <=6 判为重复（保守 5 / 激进 10）
MAX_KEEP_PER_BURST = 3              # 每组最多保留几张
BLUR_THRESHOLD = 100.0              # Laplacian 方差低于此值判模糊

# ---------------------------------------------------------------- L3 识别层
AGENT_BATCH_SIZE = 20
MIN_AGENT_CONF = 0.55               # 低于此值进入人工复核队列

# ---------------------------------------------------------------- 地点
PLACE_CLUSTER_KM = 1.0              # 1km 内视为同一地点
EARTH_R_KM = 6371.0

# ---------------------------------------------------------------- 网页
WEB_HOST = "127.0.0.1"
WEB_PORT = 8765
# 界面默认只展示置信度 >= 此值的观测。
# 实测：>=0.45 约 63 种 / 196 次，地理一致性可验证；
#       <0.30 的 379 种基本是噪声（中国出现"非洲泽鹞""艾草松鸡"等）。
UI_MIN_CONF = 0.45

# ---------------------------------------------------------------- 时区
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    from datetime import timezone, timedelta
    LOCAL_TZ = timezone(timedelta(hours=8))
