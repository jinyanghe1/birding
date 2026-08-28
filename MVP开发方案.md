# 本地观鸟数据库（「本地懂鸟」）— 调研 + MVP 开发方案

> 版本 v1.0 ｜ 2026-08-28 ｜ 目标机器：本机实测（macOS 27.0 / Apple Silicon / 21GB Photos 库）

---

## 0. TL;DR（先读这段）

1. **可行性已实测通过**：`osxphotos 0.76.1` 在你这台 macOS 27.0 上能正常读库（加载 2.9 秒，读出 15,413 张资产）。这是整个项目最大的未知数，已经排除。
2. **最大的坑不是识别，是 iCloud**：你库里 **只有 1,704 / 15,413 张本地有原图（11%）**，其余 13,693 张原图在云端。但实测 **300/300 的云端照片都有本地缩略图**（中位 191KB、宽度中位 768px）。→ **方案定为：一律用本地缩略图做识别，绝不触发原图下载。**
3. **识别层不要自建模型，用「三级漏斗」**：MegaDetector（免费，毫秒级）砍掉 85% 的非动物图 → 连拍去重再砍一半 → **剩下的精兵交给 Agent 视觉识别**（你原本的直觉是对的，这样成本才可控）。单独用 Agent 扫全库不现实。
4. **开源现成方案都不可用**：`bird-id-mcp`（唯一有中文名的）实测 0 star / 无 LICENSE 文件 / 无任何精度数据，只能当参考；`SpeciesNet` 实测缺白头鹎、画眉、麻雀、普通翠鸟等中国常见种。**都不能当主分类器。**
5. **两个免费红利**：① Photos 已自带反查好的中文地名（9,508 张有 `place`），地点字段几乎白送；② 时间/GPS/连拍信息全部从元数据拿，零成本。
6. **两个坏消息**：Photos 的 `burst` 连拍标记只有 3 张（几乎没用），必须自建「时间戳 + 感知哈希」分组；系统自带的物体识别标签 `labels_normalized` 全为 0（不可用），不能拿来预筛。
7. **MVP 建议工期**：P0 骨架 1 天 → P1 跑通全链路 2 天 → P2 网页可视化 1.5 天 → P3 Skill 封装 0.5 天。

---

## 1. 本机环境实测（数据驱动，非推测）

所有数字均为刚才在本机实测得到，非文档摘抄。

| 项目 | 实测值 | 对方案的影响 |
|---|---|---|
| 系统 | macOS 27.0 (build 26A5353q), arm64 | osxphotos 官方只测到 15.7.2，但**实测可用** |
| Photos 库大小 | 21 GB | — |
| 资产总数 | **15,413** | 14,793 图 + 620 视频 |
| DB schema | `ZASSET`（**不是** `ZGENERICASSET`） | 新版系统已改表名，裸读 SQLite 的方案要跟着改 |
| `osxphotos.PhotosDB()` 加载 | **2.9 秒** | 完全可接受，可每天跑 |
| `photos()` 返回 | 15,413 条，0.0 秒 | 元数据在内存里，快 |
| **本地有原图** | **1,704 张（11%）** | ⚠️ 核心约束 |
| iCloud-only | 13,693 张（89%） | 不能依赖 `p.path` |
| **iCloud 照片有本地缩略图** | **300/300 = 100%** | ✅ 关键救命稻草 |
| 缩略图中位大小 / 宽度 | 191 KB / **768 px**（范围 360–2048） | 够做检测，鸟占比大时够做识别 |
| 有真实经纬度 | 9,678 张（63%） | 37% 无 GPS → 需要「无坐标」降级分支 |
| **Photos 自带中文地名 `place`** | **9,508 张（62%）** | ✅ 反查地名白送，不用接高德 |
| 系统识别标签 `labels_normalized` | **0 张** | ❌ 不能用作预筛 |
| Photos `burst` 连拍标记 | **3 张** | ❌ 等于没有，必须自建分组 |
| 年份分布 | 2026: 8,307 ｜ 2025: 4,457 ｜ 2024: 843 ｜ 2023: 577 ｜ 2019: 470 | 主力数据在近两年 |
| 相机来源 | iPhone(IMG_) + Nikon(DSCN_) | 尼康连拍 → 时间戳分组必需 |
| ffmpeg | 已有（miniconda 内） | 视频抽帧可直接用 |
| Python | 3.13.12（已建 venv `birdskill`，装了 osxphotos / pillow / numpy） | 环境已就绪 |

**关键结论**：整条链路的最大技术风险（能不能读到照片库）已经排除。剩下的都是工程量。

---

## 2. 市面方案调研

### 2.1 商业 / 公益产品

| 产品 | 识别方式 | 权重/API | 覆盖 & 精度 | 可借鉴点 |
|---|---|---|---|---|
| **懂鸟**（国内） | App/小程序，离线图像+声音识别 | **有商业 API**：`hhoai.api.bdymkt.com`，**¥100/1万次**（≈¥0.01/次），500万次 ¥4000 | 全球 11,000+ 种，官方称 **Top1 85% / Top5 96%** | 国内鸟种覆盖最好，是**兜底校验**的最优解 |
| **Merlin Bird ID**（Cornell） | 闭源 CV（Visipedia 血统）+ **eBird 时空先验** | 无公开 API | ~11,000 种，官方称 ~98% | ⭐ **时空先验**是它准确率碾压的原因：先用「地点+月份」把候选从上万缩到几十。这是本项目最该抄的一招 |
| **iNaturalist / Seek** | CV 模型，**权重刻意不公开**（官方称版权原因），只放 ~500 类 small model | 有开源 REST API（匿名可读） | v2.32 覆盖 12 万+ taxa，平均 ~87.5% | **iNat2021 数据集**（1万种/270万图）可自由下载，是未来微调的语料 |
| **eBird**（Cornell） | 不做识别，只做记录与分布 | 免费 API（需 Key） | — | 分布先验数据源 |
| **SpeciesNet**（Google） | MegaDetector + EfficientNetV2-M | **完全开源**，Apache 2.0 | 见 2.3，**中国鸟种覆盖差** | 工程架构值得抄 |
| BirdNET / Perch / BirdMAE | **全是音频模型**（梅尔频谱输入） | 开源 | — | ❌ **不能用于照片**，别踩坑 |

### 2.2 已经做过的 Agent / MCP 实践

| 项目 | 做法 | 评价 |
|---|---|---|
| **`Hakureirm/bird-id-mcp`** | YOLOv8 检测 + ONNX 分类（S1v2 37MB / ConvNeXt-Tiny 144MB），**10,753 种，直接输出中文名**，纯 CPU ~150ms（作者自测） | ⚠️ **实测：0 star、0 fork、0 下载、仓库无 LICENSE 文件、2 次 commit 后再无维护、无任何精度 benchmark**。但 10,753 中文标签实测含画眉/红嘴蓝鹊/白头鹎/暗绿绣眼鸟，**中国种覆盖确实好**。→ **只做借鉴与对照，不进主链路** |
| `woodcreeper/birding-buddy-mcp` | TypeScript + eBird API + Xeno-canto，28 个 tool，含 life list 追踪 | 思路可抄（life list 概念） |
| `dmontgomery40/mcp-server-birdstats` | BirdNET-Pi + eBird 交叉分析稀有鸟种 | 音频侧，参考 |

**结论**：MCP 生态没有成熟可用的本地鸟种识别方案。识别能力得靠「开源模型 + Agent 视觉」自建，这反而说明做这个东西有价值。

### 2.3 识别模型候选对比

| 方案 | 大小 | 许可 | 中国鸟种 | arm64 部署 | 推荐 |
|---|---|---|---|---|---|
| **MegaDetector V6**（只判"有没有动物"） | 2.3M–58M 参数 | **MIT / Apache 变体**（避开 AGPL） | 不适用 | 易（ONNX） | ⭐⭐⭐⭐ **做漏斗第一级** |
| **Agent 视觉模型（我）** | — | — | 广，中文名强 | — | ⭐⭐⭐⭐⭐ **做最终判定** |
| SpeciesNet | ~224MB (ONNX) | Apache 2.0 | ❌ **实测缺**白头鹎/画眉/麻雀/普通翠鸟 | 中（需绕开 TF + Kaggle） | ⭐⭐ 不推荐 |
| timm iNat21 微调（`eva02_large_..._inat21`） | 200MB–1.2GB | **CC BY-NC（禁商用）** | 10,000 种，**无中文名** | 中，CPU 超预算 | ⭐⭐ 备选 |
| BioCLIP 零样本 | ~350MB | Apache 2.0 | 科/属级好，**种级不可靠** | 易 | ⭐ 只做粗筛 |
| Apple Vision `VNClassifyImageRequest` | 系统内置 | 闭源 | **只有 bird/heron/owl/gull 等十几个粗标签** | 极易 | ⭐ 仅辅助 |
| bird-id-mcp 模型 | 37/144MB | **无 LICENSE 文件** | ✅ 好 | 极易 | ⭐⭐⭐ 参考/对照 |

**几个明确证伪的点**（避免走弯路）：
- BirdNET / Perch / BirdMAE **都是音频模型，输入是梅尔频谱图，不能处理照片**。
- Apple Vision **没有鸟种级标签**，只能回答"这是不是鸟"。
- iNat 官方**权重不公开**，只有网站/API 能用。

---

## 3. 技术选型结论

### 3.1 三级漏斗（核心设计）

```
15,413 张
  │
  ├─ L0  元数据过滤（免费/秒级）      → 排除截图、已知非鸟类场景、重复 UUID
  │                                    剩 ~13,000
  ├─ L1  MegaDetector ONNX 检测（~30ms/张，免费）
  │      判"有没有动物"，conf ≥ 0.20   剩 ~1,500（经验：相机拍鸟的图占比通常 <15%）
  │      ⚠️ 实测必做，否则 Agent 成本炸掉
  ├─ L2  连拍分组 + pHash 去重 + 清晰度选片（免费）
  │      每组只留 1–3 张最清晰的         剩 ~300–600
  └─ L3  Agent 视觉识别（我来看图）
         输出 Top1 中文名 + 学名 + 置信度 + 辨识依据    ~300–600 次调用
```

**为什么不让 Agent 直接扫全库**：15,413 张即使只判"有没有鸟"也要 15,413 次图像调用，成本和耗时都不可接受。加了两级免费漏斗后降到 2–4%，才进入可工程化的范围。

**为什么不让本地模型做最终判定**：现有开源方案要么中国种覆盖差（SpeciesNet），要么无中文名（iNat 系列），要么无精度数据无授权（bird-id-mcp）。Agent 视觉在中文鸟种 + 疑难近缘种（各种柳莺/鹟）上的表现更可靠，而且能**给出辨识依据**（"腰羽黄色、眉纹白色"），这本身就是产品价值。

### 3.2 各层技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 照片读取 | **osxphotos 0.76.1**（Python 3.13，独立 venv） | 实测可用；直读 SQLite 工作副本，**不触发权限弹窗、不改库**；不依赖 PhotoKit |
| 图像源 | **`p.path_derivatives`（优先）→ `p.path`（兜底）** | 100% 覆盖率，避免 iCloud 下载 |
| "有没有动物" | **MegaDetector V6 MIT 变体，ONNX Runtime** | 免费、快、许可干净 |
| 去重分组 | **时间戳 gap ≤ 2s + pHash 汉明距离 ≤ 6** | 本机 burst 标记等于没有，必须自建 |
| 清晰度 | **OpenCV Laplacian 方差**，组内相对排名 | 成熟稳定 |
| Agent 识别 | **Agent 视觉（我）**，输出结构化 JSON | 中文名 + 辨识依据，零部署 |
| 二次校验（可选） | iNat 匿名 API / 懂鸟 API（¥0.01/次） | 低置信度时才用 |
| 存储 | **SQLite**（WAL 模式） | 单机、零运维、够用 |
| 网页后端 | **FastAPI + uvicorn** | 轻，和 core 模块同进程 |
| 网页前端 | **单文件 HTML + 原生 JS**（无构建） | 避免 npm 依赖地狱；图表用轻量 SVG 自绘或 CDN Chart.js |
| 定时任务 | **launchd LaunchAgent**（非 cron） | cron 在 macOS 上无 TCC 归属，最易失败 |
| 地点反查 | **`p.place`（62% 白送）→ `reverse_geocoder` 英文兜底 → 本地映射中文** | 不联网 |

---

## 4. 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│  入口 A：launchd 每日定时        入口 B：CLI（Agent / Skill）  │
│         bird scan                    bird query/stats/...     │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
                ▼                              │
┌───────────────────────────────────────────┐  │
│  birdscan/ 核心库（唯一事实来源）           │  │
│  ┌─────────┐ ┌──────────┐ ┌────────────┐ │  │
│  │ photos  │→│ pipeline │→│ identifier │ │  │
│  │ (读取)  │ │ (去重选片)│ │ (三级漏斗) │ │  │
│  └─────────┘ └──────────┘ └────────────┘ │  │
│  ┌─────────────────────────────────────┐ │  │
│  │ store/  SQLite（species/obs/photos）│ │  │
│  └─────────────────────────────────────┘ │  │
└───────────────────────────────────────────┘  │
        ▲                    ▲                 │
        │                    │                 │
┌───────┴────────┐  ┌────────┴────────┐        │
│  Web UI        │  │  FastAPI        │        │
│  localhost:8765│  │  /api/*         │        │
└────────────────┘  └─────────────────┘        │
                                               │
                    ┌──────────────────────────▼──┐
                    │  Skill: SKILL.md → 调 CLI    │
                    │  「我看过哪些鸟？」→ 查同一库 │
                    └─────────────────────────────┘
```

**双端共用同一个 SQLite + 同一个 `birdscan` 核心库**，这是保证「网页看到的」和「Skill 回答的」永远一致的关键。

---

## 5. 数据库设计（完整 DDL）

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 鸟种字典（全局唯一，跨观测复用）
CREATE TABLE species (
  id                INTEGER PRIMARY KEY,
  common_name_cn    TEXT NOT NULL UNIQUE,   -- 中文名，主显示
  common_name_en    TEXT,                   -- 英文名
  scientific_name   TEXT,                   -- 拉丁学名
  family_cn         TEXT,                   -- 科（中文）
  order_cn          TEXT,                   -- 目（中文）
  iucn_status       TEXT,                   -- LC/NT/VU/EN/CR
  china_protection  TEXT,                   -- 国家一级/二级/三有/None
  endemic_cn        INTEGER DEFAULT 0,      -- 是否中国特有种
  image_url         TEXT,                   -- 名片图（可选，缓存本地）
  created_at        TEXT NOT NULL
);

-- 观测事件：一次"看到"= 同一鸟种 + 同一天 + 同一地点簇
-- 【次数】就是这张表的行数，不冗余存计数字段
CREATE TABLE observations (
  id              INTEGER PRIMARY KEY,
  species_id      INTEGER NOT NULL REFERENCES species(id),
  obs_date        TEXT NOT NULL,            -- YYYY-MM-DD（本地时区）
  obs_time        TEXT,                     -- HH:MM:SS 首次命中时间
  -- 地点（三档降级）
  latitude        REAL,
  longitude       REAL,
  place_name      TEXT,                     -- Photos 自带中文地名，优先
  place_source    TEXT,                     -- 'photos' | 'reverse' | 'none'
  admin_region    TEXT,                     -- 省/市，用于分组统计
  -- 汇总
  photo_count     INTEGER DEFAULT 1,        -- 该次观测保留了几张图
  best_sharpness  REAL,                     -- 代表图清晰度分
  confidence      REAL,                     -- 识别置信度 0-1
  identified_by   TEXT,                     -- 'agent' | 'model' | 'manual'
  notes           TEXT,                     -- 辨识依据（Agent 产出）
  created_at      TEXT NOT NULL
);
CREATE INDEX idx_obs_species ON observations(species_id);
CREATE INDEX idx_obs_date    ON observations(obs_date);
CREATE INDEX idx_obs_place   ON observations(place_name);

-- 照片：观测事件的证据图，一次观测保留 1-3 张
CREATE TABLE photos (
  id              INTEGER PRIMARY KEY,
  obs_id          INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
  asset_uuid      TEXT NOT NULL UNIQUE,     -- Photos 资产 UUID，幂等键
  filename        TEXT,
  shot_at         TEXT,                     -- 拍摄时间（ISO8601 带时区）
  media_type      TEXT,                     -- 'image' | 'video_frame'
  video_offset_sec REAL,                    -- 视频抽帧的秒偏移
  -- 图像来源（关键：优先缩略图）
  image_source    TEXT,                     -- 'derivative' | 'original'
  image_path      TEXT,                     -- 绝对路径（缩略图路径，不复制文件）
  thumb_cache     TEXT,                     -- 自建缓存的小图，供网页秒开
  width           INTEGER,
  height          INTEGER,
  sharpness       REAL,                     -- Laplacian 方差
  phash           TEXT,                     -- 16 位 hex，去重用
  is_representative INTEGER DEFAULT 0,      -- 是否为该次观测的代表图
  exif_json       TEXT                      -- 原始元数据留档
);
CREATE INDEX idx_photo_obs ON photos(obs_id);
CREATE INDEX idx_photo_phash ON photos(phash);

-- 已扫描资产表（增量扫描的水位线 + 跳过名单）
CREATE TABLE scanned_assets (
  asset_uuid     TEXT PRIMARY KEY,
  scanned_at     TEXT NOT NULL,
  stage          TEXT NOT NULL,   -- 'l0_skipped' | 'l1_no_animal' | 'l2_dedup' | 'l3_identified'
  skip_reason    TEXT,
  animal_conf    REAL             -- L1 检测置信度，留档便于调阈值
);
CREATE INDEX idx_scanned_at ON scanned_assets(scanned_at);

-- 待识别队列（L2 产出的精兵，等 Agent 批量处理）
CREATE TABLE id_queue (
  id            INTEGER PRIMARY KEY,
  asset_uuid    TEXT NOT NULL UNIQUE,
  image_path    TEXT NOT NULL,
  shot_at       TEXT,
  latitude      REAL, longitude REAL,
  place_name    TEXT,
  burst_group   TEXT,
  sharpness     REAL,
  status        TEXT DEFAULT 'pending',  -- pending | done | failed | rejected
  result_json   TEXT,
  created_at    TEXT NOT NULL
);

-- 扫描日志
CREATE TABLE scan_runs (
  id            INTEGER PRIMARY KEY,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  new_assets    INTEGER DEFAULT 0,
  l1_passed     INTEGER DEFAULT 0,
  l2_kept       INTEGER DEFAULT 0,
  l3_identified INTEGER DEFAULT 0,
  new_species   INTEGER DEFAULT 0,
  status        TEXT,             -- running | ok | error
  error_msg     TEXT
);
```

**设计要点**：
- **`species` 与 `observations` 分离**：鸟种是字典（唯一），观测是事件（可多次）。「我看过哪些鸟」= 查 species；「这个鸟看过几次」= count(observations)。
- **次数不冗余存**：避免更新不一致，永远用 `COUNT(*)` 实时算（15,413 的量级，SQLite 毫秒级）。
- **`scanned_assets` 是水位线**：已处理过的 UUID 永不重复识别，增量扫描靠它。
- **`id_queue` 解耦**：L2 是 CPU 密集（快），L3 是 Agent 调用（慢、可能中断），分开跑，L3 中断后可从队列恢复。

---

## 6. 目录结构

```
观鸟skill/
├── SKILL.md                      # Skill 定义（Agent 读这个）
├── README.md
├── pyproject.toml
├── venv/                         # 或复用 ~/.workbuddy/binaries/python/envs/birdskill
│
├── birdscan/                     # 核心库（Web 与 CLI 共用）
│   ├── __init__.py
│   ├── config.py                 # 所有阈值/路径集中在这里，便于调参
│   ├── photos.py                 # L0: osxphotos 封装，增量拉取 + 图像源选择
│   ├── dedup.py                  # L2: 连拍分组 + pHash 去重 + 清晰度选片
│   ├── detector.py               # L1: MegaDetector ONNX 封装
│   ├── identify.py               # L3: 队列管理 + 结果落库
│   ├── species.py                # 鸟种字典的 upsert / 归一化 / 别名合并
│   ├── geo.py                    # 地点三档降级 + 地点簇聚类
│   ├── store.py                  # SQLite 全部读写（唯一入口）
│   └── cli.py                    # typer/argparse CLI
│
├── web/
│   ├── server.py                 # FastAPI
│   ├── static/index.html         # 单页应用（无构建）
│   └── static/app.js
│
├── scripts/
│   ├── scan_daily.sh             # launchd 调用入口
│   ├── bootstrap.sh              # 一次性建库 + 全量扫描
│   └── com.hejinyang.birdscan.plist
│
├── models/                       # MegaDetector ONNX（gitignore）
├── data/
│   ├── birds.db                  # SQLite
│   ├── thumbs/                   # 自建缩略图缓存
│   └── species_seed.csv          # 中国鸟种名录种子数据
│
└── tests/
    ├── test_dedup.py
    └── fixtures/                 # 手工标注的 100 张照片，做精度回归
```

---

## 7. 逐模块实现规格（细粒度）

### 7.1 `config.py` — 集中阈值

```python
LIBRARY_PATH = None                      # None = 系统默认库
DB_PATH      = "data/birds.db"
THUMB_DIR    = "data/thumbs"

# L0 元数据过滤
SKIP_IF_SCREENSHOT   = True              # UTI == public.png 且尺寸异常 → 跳过
MIN_PIXELS           = 200_000           # 小于 20 万像素跳过

# L1 检测
DETECTOR_ONNX   = "models/md_v6_mit_yolov9_c.onnx"
DETECT_CONF     = 0.20                   # 动物检测阈值（官方经验值 0.15–0.30）
DETECT_CLASSES  = {1: "animal"}          # MD 类别: 0=blank? 按实际模型定

# L2 连拍分组 + 去重
BURST_GAP_SEC     = 2.0                  # 相邻两张时间差 ≤2s 视为同一组
PHASH_SIZE        = 8                    # 64 位哈希
PHASH_DUP_DIST    = 6                    # 汉明距离 ≤6 判为重复（保守 5 / 激进 10，取 6）
MAX_KEEP_PER_BURST = 3                   # 每组最多保留 3 张
BLUR_THRESHOLD    = 100.0                # Laplacian 方差 <100 判模糊（组内相对排名后用）

# L3 识别
AGENT_BATCH_SIZE   = 20                  # 每批交给 Agent 的张数
MIN_AGENT_CONF     = 0.55                # 低于此值标为 needs_review
FALLBACK_API       = None                # 可选: 'dongniao' | 'inat'

# 地点
PLACE_CLUSTER_KM   = 1.0                 # 1km 内视为同一地点
PLACE_CLUSTER_DAYS = 1                   # 同一天 + 同一地点簇 = 一次观测
```

### 7.2 `photos.py` — L0 读取层（最关键，已实测）

```python
def open_library() -> osxphotos.PhotosDB:
    """实测：加载约 2.9s。每次进程只开一次，全局复用。"""

def get_image_source(photo) -> tuple[str, str] | None:
    """
    返回 (source_kind, abs_path)。核心逻辑：
      1. if photo.path_derivatives:  取 size 最大的那个 → ('derivative', path)
         # 实测：iCloud 照片 100% 有，中位 768px / 191KB
      2. elif photo.path:            → ('original', path)
         # 只有 11% 的照片走这条
      3. else:                       return None（标记 skip）
    ⚠️ 绝不调用 export(use_photos_export=True)，那会触发 iCloud 下载，
       在 launchd 无 GUI 会话下会卡死。
    """
```

```python
def iter_new_assets(since: datetime | None) -> Iterator[Asset]:
    """
    增量拉取。要点：
      - 用 QueryOptions(added_after=since)，首次全量传 None
      - ⚠️ osxphotos 已知坑：TOML/CLI 里日期必须带时间，纯日期会报
        'dt must be type datetime.datetime'
      - 每个 asset 提取：
          uuid, original_filename, date(拍摄时间), date_added,
          latitude, longitude, place（Photos 自带中文地名）,
          original_width/height, burst, ismovie, uti
      - ⚠️ 判 GPS 用 `p.latitude is not None`，不要用 `p.location`
        （实测 p.location 会返回 (None, None) 这种 truthy 的元组）
      - ⚠️ p.path_derivatives 是**属性**不是方法，别加括号
      - 跳过 scanned_assets 里已有的 uuid（幂等）
    """
```

```python
def extract_video_frames(photo, max_frames=8) -> list[tuple[float, str]]:
    """
    视频处理（620 个视频）：
      1. ffprobe 取时长
      2. 时长 ≤30s → ffmpeg -i in.mov -vf fps=1 -q:v 4 f_%03d.jpg
         时长 >30s → ffmpeg -skip_frame nokey ...（只抽关键帧，最省）
      3. 抽出的帧当普通图片走 L1/L2，命中后 video_offset_sec 记录秒偏移
    """
```

### 7.3 `detector.py` — L1 有无动物

```python
class AnimalDetector:
    """MegaDetector V6 ONNX。选 MIT 或 Apache 变体避开 AGPL。
    输入: 图片路径 → 输出: (has_animal: bool, max_conf: float, boxes: list)
    批量推理，onnxruntime 的 CPUExecutionProvider（M 系列上够快）
    """
    def detect(self, image_path) -> Detection
    def detect_batch(self, paths) -> list[Detection]   # 实测目标 ~30ms/张
```

**注意**：MegaDetector 官方已下架性能数字（承认验证集可能损坏），网上的 "82.8% recall" 是第三方镜像数字，**不要引用**。阈值要自己在本机 100 张标注集上调。

### 7.4 `dedup.py` — L2 连拍去重 + 选片（你的核心诉求）

```python
def group_bursts(assets) -> list[list[Asset]]:
    """
    实测本机 Photos 的 burst 标记只有 3 张（等于没用），必须自建：
      按 shot_at 排序，相邻间隔 ≤ BURST_GAP_SEC(2s) 归为一组。
    尼康相机连拍 5-10 张，这层能砍掉 70-90%。
    """

def phash_of(image_path) -> str:
    """imagehash.phash(Image.open(p), hash_size=8). 返回 16 位 hex。
    选 pHash 而非 dHash：DCT 低频，对缩放/压缩/亮度变化更鲁棒。"""

def dedup_group(group) -> list[Asset]:
    """
    组内两两算汉明距离，≤ PHASH_DUP_DIST(6) 视为同一张。
    贪心聚类，每簇保留 sharpness 最高的 1 张。
    连拍帧间有轻微位移/曝光差 → 阈值取 6（5 太紧会漏并，10 太松会误删）
    """

def sharpness_of(image_path) -> float:
    """
    cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    经验带：<50 明显糊 | 50-150 borderline | >200 清晰 | >2000 多半是截图
    ⚠️ 关键：必须先分组去重再比清晰度，
       否则整组都糊时会把整组全删掉，一张不留。
    """

def pick_representatives(group, max_keep=3) -> list[Asset]:
    """
    组内综合打分：
      score = 0.30 * norm(sharpness)
            + 0.25 * norm(检测框面积占比)   # 鸟占画面越大越好
            + 0.20 * norm(原图像素)
            + 0.15 * 位置分(连拍中间帧优先，两端常有抖动)
            + 0.10 * 曝光分(直方图两端裁剪越少越好)
    按 score 降序取前 max_keep 张 → 写 is_representative
    """
```

### 7.5 `identify.py` — L3 Agent 识别

```python
def build_batch(limit=20) -> list[QueueItem]:
    """从 id_queue 取 status='pending'，按 shot_at 排序，最多 20 张。"""

def apply_result(uuid, result: dict):
    """
    result = {
      "common_name_cn": "红嘴蓝鹊",
      "scientific_name": "Urocissa erythroryncha",
      "confidence": 0.92,
      "is_bird": true,
      "notes": "尾长、喙红、头黑白色，飞行时尾羽明显"   # 辨识依据，产品价值点
    }
    落库流程：
      1. is_bird == false → status='rejected'，从库里剔除
      2. species.upsert(common_name_cn)  → species_id
         ⚠️ 别名合并：同一物种的不同中文叫法要归一化
            （如"红嘴蓝鹊" vs "红嘴蓝鹊（中国）"）
      3. 地点簇聚类 → place_name
      4. observations 查重：同 species + 同 obs_date + 同地点簇
         → 存在则 photo_count++；不存在则新建（这就是"次数"）
      5. photos 写入，is_representative=1 的那张更新 obs.best_sharpness
      6. id_queue.status='done'
    """
```

**Agent 识别的 prompt 要点**（写在 SKILL.md 里）：
- 必须给出中文名 + 拉丁学名，不确定时给"属/科"级别而非瞎猜种
- 必须给辨识依据（1 句话）
- 明确允许输出 `is_bird: false` 和 `confidence < 0.55`
- **传入地点和时间**：让 Agent 用时空先验缩小候选（抄 Merlin 的做法）

### 7.6 `geo.py` — 地点三档降级

```python
def resolve_place(photo) -> tuple[str, str]:
    """
    实测 62% 的照片 Photos 已反查好中文地名 → 直接用，零成本。
      1. photo.place 有值        → (place_name, 'photos')   ← 62%
      2. 有经纬度 → reverse_geocoder（离线，英文）
                  → 本地映射表转中文                        ← 37%
      3. 都没有                   → (None, 'none')          ← 降级，
         用同批次相邻照片的地点推断
    ⚠️ 不要接高德/百度：需要 key、联网、有配额，
       而 p.place 已经免费覆盖了大部分场景。
    """

def cluster_location(lat, lon) -> str:
    """1km 网格 + 已有 place_name 归一化，用于判定"同一地点"。"""
```

### 7.7 `store.py` — 唯一数据入口

所有 SQL 都收敛在这一个文件，Web 和 CLI 都只调它。提供：
`get_species_list(order_by='count'|'recent')`、`get_species_detail(id)`、
`get_observations(species_id, date_from, date_to, place)`、
`get_stats()`、`get_timeline()`、`upsert_species()`、`add_observation()`。

---

## 8. Web UI 可视化设计（localhost:8765）

单文件 HTML，无构建，暗色主题（符合你的 PPT/UI 偏好：统一字体、克制配色、最多 2 个主色）。

### 页面 1：物种墙（首页）
- 卡片网格：缩略图 + 中文名 + 学名 + **观测次数徽章** + 最近观测日期
- 顶部统计条：**累计 X 种** / 累计 Y 次观测 / 本月新增 Z 种 / 覆盖 W 个地点
- 排序切换：按次数 / 按最近观测 / 按科属
- 搜索框：中文名、学名、地点全匹配

### 页面 2：物种详情
- 大图轮播（该次观测保留的 1–3 张）
- 辨识依据（Agent 的 notes）
- **观测时间轴**：每一次观测一个点，标注日期 + 地点
- 同科/同属的其他已观测鸟种推荐

### 页面 3：可视化面板（你要求的可视化重点）
1. **观测热力日历**（GitHub 贡献图风格）：一年 365 格，颜色深浅 = 当天观测次数。一眼看出哪几个月鸟多。
2. **地点分布图**：按 `place_name` 聚合的横向条形图 Top 10；有经纬度的画散点（用轻量 SVG 自绘中国轮廓，或简单经纬度散点，**不引外部地图依赖**）。
3. **累计种数增长曲线**：按首次观测日期累积，看"加新"速度。
4. **科属分布旭日图/树图**：雀形目 > 鹟科 > ...
5. **时段分布**：观测时间的小时分布（鸟在清晨活跃的规律一眼可见）。

### API 端点
```
GET  /api/stats                      累计种数/次数/地点数/本月新增
GET  /api/species?order=count        物种列表（含次数、代表图、首末观测）
GET  /api/species/{id}               详情 + 所有观测 + 图片
GET  /api/observations?from&to&place 观测流水
GET  /api/calendar?year=2026         热力日历数据
GET  /api/places                     地点聚合 Top N
GET  /api/timeline                   累计增长曲线
GET  /thumb/{photo_id}               缩略图（从缓存读，秒开）
POST /api/scan                       手动触发扫描
POST /api/review                     人工修正鸟种（低置信度复核入口）
```

---

## 9. Skill 封装（双端统一）

`SKILL.md` 的 frontmatter 与能力声明：

```yaml
name: birding
description: 本地观鸟数据库。当用户问"我看过哪些鸟""这个鸟在哪拍的"
             "今年加新了几种""帮我扫描新增照片里的鸟"时使用。
             读取本地 Photos 库中的鸟类照片，维护鸟种/图片/时间/地点/次数。
```

**Agent 通过 CLI 调用，与网页共用同一个 SQLite**：

```bash
bird stats                              # 累计种数/次数/地点
bird list --order count --limit 20      # 「我看过哪些鸟」→ 按次数排
bird show 红嘴蓝鹊                       # 某鸟的全部观测 + 图片路径
bird search --place 奥森 --month 5      # 「五月在奥森拍到什么」
bird scan                               # 增量扫描（L0+L1+L2）
bird queue --take 20                    # 取待识别批次 → Agent 看图 → 回写
bird review --low-confidence            # 列出需要人工复核的
```

**对话示例**：
> 用户："我看过哪些鸟？"
> Agent：`bird list --order count` → 读到 15 种 / 43 次观测 → 用自然语言回答，并附网页链接 http://localhost:8765

> 用户："今年加新了几种？"
> Agent：`bird stats --new-since 2026-01-01` → 回答

**关键**：Skill 里明确写「数据库是唯一事实来源，不要自己维护列表」，避免 Agent 缓存一份导致双端不一致。

---

## 10. 分阶段里程碑

### P0 — 骨架与数据底座（0.5–1 天）
| # | 任务 | 验收标准 |
|---|---|---|
| P0-1 | 建 venv、装依赖（osxphotos / pillow / numpy / opencv / imagehash / onnxruntime / fastapi） | `pip list` 干净，venv 可复现 |
| P0-2 | `store.py` + 全部 DDL + 迁移脚本 | `bird init` 生成 birds.db，表结构正确 |
| P0-3 | `photos.py` 只读探针 | 打印：总资产数、有 GPS 数、**有 derivatives 数**、有 place 数（对照本文 §1 实测值） |
| P0-4 | `cli.py` 骨架 + config.py | `bird stats` 能跑通（哪怕全是 0） |

**P0 出口**：能稳定读出 15,413 条资产元数据，且**不触发任何权限弹窗**。

### P1 — 全链路跑通（2 天）
| # | 任务 | 验收标准 |
|---|---|---|
| P1-1 | 下载 MegaDetector ONNX（MIT 变体）到 `models/` | `bird detect <一张鸟图>` 返回 animal conf > 0.2 |
| P1-2 | `detector.py` 批量推理 | 1,000 张耗时可测，写入 `scanned_assets.animal_conf` |
| P1-3 | `dedup.py` 连拍分组 + pHash + 清晰度 | 用 20 张已知连拍做单测：分组正确、每组留 1–3 张最清晰的 |
| P1-4 | `geo.py` 三档降级 | 9,508 张能拿到中文地名 |
| P1-5 | `identify.py` 队列 + 落库 | 手工构造 10 条 result 写入，observations/photos 正确 |
| P1-6 | 端到端小样本：`bird scan --limit 500` | 完整走完 L0→L1→L2，产出 id_queue |
| **P1-7** | **精度回归**：手工标注 100 张（50 张鸟 / 50 张非鸟） | **L1 召回率 ≥ 90%，L2 去重后无"整组被删光"的情况** |

**P1 出口**：`bird scan` 能在无人值守下跑完全库，id_queue 里有几百张待识别的精兵。

### P2 — 识别 + 网页可视化（1.5 天）
| # | 任务 | 验收标准 |
|---|---|---|
| P2-1 | Agent 批量识别（分批 20 张，含时空先验提示） | 完成 id_queue 全量，落库 |
| P2-2 | 人工复核页（低置信度 < 0.55 的） | 能一键改鸟种，改动写回 |
| P2-3 | FastAPI + 全部 /api 端点 | 每个端点返回正确 JSON |
| P2-4 | 网页：物种墙 + 统计条 | 缩略图秒开，次数正确 |
| P2-5 | 网页：物种详情 + 时间轴 | 图片轮播正常 |
| P2-6 | 可视化面板：热力日历 / 地点 Top10 / 累计曲线 / 时段分布 | 4 张图全部渲染 |
| P2-7 | 中国鸟种名录种子数据（中文名/学名/科/目/保护级别） | `species` 表预填，识别结果自动关联 |

**P2 出口**：浏览器打开 localhost:8765 能看到完整的个人观鸟档案。

### P3 — 自动化 + Skill 封装（0.5 天）
| # | 任务 | 验收标准 |
|---|---|---|
| P3-1 | `scripts/scan_daily.sh` | 手动执行成功，日志写到 `logs/` |
| P3-2 | launchd LaunchAgent plist | `launchctl load` 后每天 21:00 自动跑 |
| P3-3 | `SKILL.md` 编写 | Agent 能正确理解何时调用、如何调用 |
| P3-4 | 端到端对话测试 | 问"我看过哪些鸟"能正确回答 |

### P4 — 增强（按需，非 MVP）
- 懂鸟 API 二次校验（低置信度时，¥0.01/次）
- iNat 匿名 API 校验 + 分布先验（抄 Merlin）
- 声音识别（BirdNET，处理视频音轨）
- 本地微调（用 iNat2021 中国区数据）
- 导出 eBird/中国观鸟记录中心格式

---

## 11. 定时任务与权限（macOS 特有问题）

**用 launchd，不要用 cron**。cron 在 macOS 上没有 TCC 归属，最容易失败。

```xml
<!-- ~/Library/LaunchAgents/com.hejinyang.birdscan.plist -->
<key>ProgramArguments</key>
<array>
  <string>/Users/hejinyang/.workbuddy/binaries/python/envs/birdskill/bin/python</string>
  <string>/Users/hejinyang/WorkBuddy/观鸟skill/birdscan/cli.py</string>
  <string>scan</string>
</array>
<key>StartCalendarInterval</key>
<dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer></dict>
<key>StandardOutPath</key><string>.../logs/scan.log</string>
<key>RunAtLoad</key><false/>
```

**权限要点**：
1. osxphotos 走「复制 SQLite 工作副本」的方式读取，**不需要 Photos 授权，不会弹窗**（实测确认）。
2. `~/Pictures` 不属于 Desktop/Documents/Downloads 三个 TCC 受保护目录。若被拒，给 **venv 里的那个 python 解释器**（不是脚本路径）加「完全磁盘访问权限」。
3. TCC 认的是**可执行文件**，给 .py 脚本路径授权无效。
4. **首次务必在 Terminal.app 里手动跑一次**完成试错，再挂 launchd。
5. 因为不调用 `use_photos_export`，无 GUI 会话也能跑。

---

## 12. 风险清单与降级方案

| 风险 | 实测/判断 | 降级方案 |
|---|---|---|
| **iCloud 原图缺失（89%）** | ✅ 已确认 | 用 `path_derivatives` 缩略图（100% 覆盖）。**禁用** `export(use_photos_export=True)` |
| **缩略图分辨率偏低（中位 768px）** | ✅ 已确认 | 检测够用；识别时若鸟占画面小，标记 `low_res` 走原图按需下载队列（人工触发） |
| **osxphotos 在新系统上失效** | ✅ 实测可用，但官方只测到 15.7.2 | 警告 `psi.sqlite 缺失` 无害；若未来挂了，退回裸读 `ZASSET` 表（注意新 schema 表名） |
| **MegaDetector 精度无官方数据** | ⚠️ 官方已下架数字 | P1-7 用 100 张标注集自测；阈值调不好就放宽到 0.15（宁可多进 L3） |
| **Agent 识别的中文名不一致** | ⚠️ 高概率 | `species.py` 做别名归一化 + 用拉丁学名做二次合并键 |
| **无 GPS 的 37%** | ✅ 实测 9,678/15,413 | 用同批次相邻照片地点推断；实在没有就存 `place_name=NULL`，不影响其它字段 |
| **连拍分组误并** | ⚠️ 中 | 阈值 2s 可调；再加 pHash ≤6 双重确认后才合并 |
| **launchd 静默失败** | ⚠️ 中 | 日志写文件 + `scan_runs` 表记录；连续 3 天无记录则告警 |
| **bird-id-mcp 无 LICENSE** | ✅ 实测 | **不采用**，只做对照参考。若要用，必须先本地归档权重并自行评估授权 |
| **懂鸟 API 成本** | — | 只用于低置信度复核（估计 <5% 的图），成本可忽略 |

---

## 13. 验收标准（MVP 完成的定义）

1. `bird scan` 在无人值守下跑完 15,413 张，不弹窗、不下载原图、不崩溃。
2. 扫描后 `id_queue` 中**人工抽查 50 张，鸟类命中率 ≥ 80%**（即 L1+L2 的精度）。
3. 数据库中有 ≥ 10 个鸟种、≥ 30 条观测记录，每条有图片 / 时间 / 地点（或明确标记 NULL）。
4. 浏览器打开 `http://localhost:8765`，物种墙、详情、4 张可视化图全部正常。
5. 在同一个会话里问 Agent「我看过哪些鸟」，回答内容与网页**完全一致**（同一数据源）。
6. 连拍测试：喂一组 8 张连拍，最终入库 1–3 张，且是最清晰的。
7. `launchctl` 挂上后，第二天 21:00 自动跑完，`scan_runs` 有成功记录。

---

## 14. 附：调研来源

- osxphotos: https://pypi.org/project/osxphotos/ ｜ https://rhettbull.github.io/osxphotos/API_README.html
- MegaDetector V6: https://microsoft.github.io/Pytorch-Wildlife/model_zoo
- SpeciesNet: https://github.com/google/cameratrapai ｜ https://www.kaggle.com/models/google/speciesnet
- bird-id-mcp: https://github.com/Hakureirm/bird-id-mcp （⚠️ 0 star / 无 LICENSE）
- iNat2021 数据集: https://github.com/voxel51/inaturalist-2021 ｜ timm 微调: https://huggingface.co/collections/rwightman/inaturalist-2021-fine-tunes
- BioCLIP: https://arxiv.org/pdf/2311.18803.pdf
- iNaturalist API: https://api.inaturalist.org/v1/docs/
- 懂鸟 API: https://apis.baidu.com/store/detail/ee97e453-2ce8-44b6-b04c-bd06fa484b5e
- Merlin（时空先验）: https://www.birds.cornell.edu/home/the-magic-of-merlin
- Apple Vision taxonomy: https://developer.apple.com/documentation/vision/classifying_images
- reverse_geocoder: https://github.com/thampiman/reverse-geocoder ｜ geotool-cn: https://pypi.org/project/geotool-cn/
- CBR 中国鸟类名录: https://aviceda.org/zh/checklist/china_birds_checklist_avilist_v12.html ｜ Avibase: https://avibase.bsc-eoc.org/checklist.jsp?region=cn
