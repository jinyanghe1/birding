# 观鸟 Skill 项目长期笔记

## 项目目标
本地观鸟数据库（"本地懂鸟"）：扫描 macOS Photos → 自动发现新增鸟类照片/视频 → 去重 → 挑清晰图 → 识别鸟种 → 入库（鸟种/图片/时间/地点/次数）→ localhost 网页浏览 + 封装成 Agent Skill。双端（Web + Skill）共用同一个 SQLite。

## 硬约束（来自本机实测，改方案前必读）
1. **只能用本地缩略图 `p.path_derivatives`**，绝不能调用 `export(use_photos_export=True)` —— 89% 原图在 iCloud，强行下载会在 launchd 无 GUI 会话下卡死。
2. **连拍分组必须自建**：Photos 的 `burst` 标记实际只有 3/15,413 张，等于没有。用「时间戳间隔 ≤2s 分组 + pHash 汉明距离 ≤6 去重」。
3. **不能依赖 Photos 的系统标签**：`labels_normalized` 全为 0。
4. **DB schema 是 `ZASSET`**（不是老教程里的 `ZGENERICASSET`），裸写 SQL 时注意。
5. **定时任务用 launchd，不用 cron**（cron 在 macOS 无 TCC 归属）。
6. **照片库读取受 macOS TCC 管控**（2026-09-02 实测）：后台自动化可能拿不到权限，
   报 `Operation not permitted`。显式指定库路径只能跳过 plist 查找，救不了源文件读取。
   已加 `BIRDSCAN_LIBRARY_PATH` 环境变量（config.py），TCC 本身需用户在
   系统设置 → 隐私与安全性 → 完全磁盘访问权限 里授权，或在 GUI 会话下手动跑一次触发弹窗。
   排查时先查 `scan_runs` 表：若历史有成功记录而当前失败，即为权限问题而非代码问题。
   **2026-09-03 已证伪**：直接 `open(Photos.sqlite,"rb")` 同样 PermissionError，
   说明是**文件级 TCC 拒绝，不是 osxphotos 的拷贝策略问题**。
   → 不要再尝试「绕开 copy 直读数据库」等代码层方案，只能等用户在
     系统设置 → 隐私与安全性 → 完全磁盘访问权限 里授权，或在 GUI 会话下手动跑一次。

## 数据质量：用 `species.in_china` 筛误识别（2026-09-03 新增）
- `species.in_china` 取值：-1 未知 / 0 非中国 / 1 中国。
- 生成任何识别报告时固定用 `in_china=0 AND confidence<0.45` 筛疑似误识别，比看名字判断靠谱。
- 当前状态：516 观测 / 387 种，其中高置信(≥0.45)仅 68 条 37 种；
  待复核 448 条 369 种，`in_china=0` 占 235 种 276 条（企鹅/蜂鸟/巨嘴鸟/几维等，全是误识别）。
- 待复核积压已远超可信数据，恢复扫描后应优先复核而非继续堆新数据。

## 已建立的 Python 环境
- venv 路径：`/Users/hejinyang/.workbuddy/binaries/python/envs/birdskill`（Python 3.13.12）
- 已装：osxphotos 0.76.1 / pillow / numpy
- 待装：opencv-python / imagehash / onnxruntime / fastapi / uvicorn / typer

## 架构决策记录
- 识别采用**三级漏斗**而非单点模型：MegaDetector（免费筛动物）→ 连拍去重选片 → Agent 视觉精识别。理由：单独用 Agent 扫 1.5 万张成本不可控；现有开源模型要么中国鸟种覆盖差、要么无中文名、要么无授权无精度数据。
- 抄 Merlin 的**时空先验**思路：识别时把「拍摄月份 + 地点」一起传给 Agent，缩小候选集。
- 鸟种字典（species）与观测事件（observations）分离；**次数不冗余存字段**，永远用 `COUNT(*)` 实时算。

## L3 识别的当前实现（重要，换方案前必读）
1. **当前会话的模型不支持读图**，所以「Agent 看图识别」这条路径在本会话跑不了。
   已改为本地开源模型：`birdscan/birddet.py`（YOLO 框鸟）+ `birdscan/classifier.py`
   （ConvNeXt 分类，10,753 种带中文名）。
2. **必须先检测裁剪再分类**。整图直推会因为鸟占比太小而彻底失效。
3. 权重在 `models/`，从 HF `Hakureirm/bird-id-models` 下载。
   该仓库 0 star、无 LICENSE 文件，只能当原型，不要对外分发。
4. **置信度 ≥0.45 才可信**（`config.UI_MIN_CONF`）。<0.30 的 379 个物种基本是噪声，
   数据库里保留但界面默认过滤，等具备多模态能力时复核。
5. 若将来换成多模态模型识别，走 `bird queue --take N --prompt` → 看图 →
   `bird apply result.json`，接口已经留好，与本地模型并存不冲突。

## 定时任务
- launchd plist 已写好（`scripts/com.hejinyang.birdscan.plist`，plutil 校验通过），
  但**本沙箱内 launchctl 注册被拦截**，需要用户在沙箱外手动 load。
- 当前实际生效的是 WorkBuddy automation：`automation-1787917207670`（每天 21:00）。

## 部署架构（2026-08-29 新增）

**生产环境**：腾讯云轻量应用服务器（124.223.171.149）
- **架构**：Nginx (80) → FastAPI (8765) → SQLite
- **systemd 服务**：`/etc/systemd/system/birding.service`（自动重启）
- **一键部署**：`./scripts/deploy_to_server.sh [--data]`
- **访问地址**：http://124.223.171.149
- **详细文档**：`docs/腾讯云部署指南.md`
- **控制台信息（Lighthouse MCP 已连接，2026-08-31 确认）**：
  - InstanceId `lhins-3jodviz2`，名称 Ubuntu-1GSt，地域 **ap-shanghai**（不是北京）
  - 套餐 bundle_starter_mc_promo_med2_01：2 核 2GB / 40GB SSD / 3Mbps，包年包月
  - 到期 2027-08-29，续费方式 NOTIFY_AND_MANUAL_RENEW
  - 流量包：**200GB/月**（周期每月 29 日重置），实际用量 <30MB/月，无超额
  - 控制台防火墙：只放行 22(SSH)/80(HTTP)/ICMP，与安全协议一致；实例内 ufw 未启用（不重复开）
  - 快照：**无任何快照**，安全协议里「每天凌晨 3 点备份」尚未落地

**本地开发**：localhost:8765
- 扫描/识别/复核/手动导入都在本地做
- 数据更新后跑 `bird export-site` → `./scripts/deploy_to_server.sh --data` 同步到服务器

**GitHub Pages**：https://jinyanghe1.github.io/birding/
- 只读分享，不能上传
- 静态导出：`bird export-site` → `docs/` → push

## 关键经验（来自本次部署）

1. **SSH 用户是 `ubuntu` 不是 `root`**（腾讯云 Ubuntu 默认）
2. **FastAPI 需要 `python-multipart`**（处理文件上传）
3. **macOS tar 有扩展属性**，Linux 解压会报 `LIBARCHIVE.xattr` 警告（不影响功能）
4. **数据目录结构**：`data/birds.db` + `data/thumbs/`，打包时分开传（代码 63KB，数据 37MB）
5. **Nginx 反向代理**：`proxy_pass http://127.0.0.1:8765`，超时时间要设长（300s）
6. **Linux 权限链**：`www-data` 要读 `/home/ubuntu/...`，每一级目录都要 `chmod 755`
7. **百度地图瓦片免 key**：`maponline{s}.bdimg.com/tile/?qt=tile&x={x}&y={y}&z={z}&styles=pl&scaler=1`
8. **OSM 地图瓦片免 key**：`https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`，公共服务器无需注册

## 2026-10-14 服务器现状与安全处置（部署前必读）

**API Key 已轮换**（旧 key 曾泄露于公开仓库 tests/e2e_test.py:76,105）：
- 新 key 在本地 `data/.api_key`（勿写入任何进 git 的文件）；服务器 key 在 systemd **drop-in** `/etc/systemd/system/birding.service.d/override.conf`（不在主单元文件，改 key 要改这里）；DB users owner 行两边已同步。
- 遗留：e2e 测试仍写死旧 key（已失效），下一步改读环境变量。

**服务器真实状态（SSH 必须带 `-i ~/Downloads/birding.pem`，沙箱不带 key 连不上）**：
- 服务器代码 = 2026-09-01 部署（origin/main 时代），**不含**本地领先的 3 个 commit（相册删除/亮色主题/手机适配）和 config.py 的 BIRDSCAN_LIBRARY_PATH 改动。
- **相册已有真实使用：4 用户（owner + 成员「妈妈」+ 2 个 pending 占位行）、4 相册、1 邀请码；cloud_photos=0**（上传从未成功）。
- **⚠️ `deploy --data` 会用本地库（users=1/albums=0）抹掉服务器家庭数据**——数据部署前必须先 SSH 备份 + 定合并策略。
- 观测数据：本地 516/387（10-02），服务器 509/384（08-29）。

**已确认的代码病灶**（详见 `docs/观鸟网站技术报告-2026-10-14.md`）：
- P0-2：相册 6 端点 500（v1→v2 漏移植 4 个函数 + list_album_photos 实参不匹配）
- P0-3：存量库 cloud_photos.album_id NOT NULL 导致上传必败；file_hash 无 UNIQUE
- P0-4：POST /api/scan 无鉴权
- P1-11：相册上传无类型白名单（存储型 XSS 可窃取 key）

## 开发规范（长期记忆，必须遵守）

**服务器直接开发调试**（2026-09-01 更新）：
1. **本地写代码** → 本地简单验证（语法/导入）
2. **push 到 GitHub**（细粒度 commit，一个功能一个 commit）
3. **服务器直接调试**：`ssh` 到服务器，`git pull` 后直接改代码调试
4. **调试通过后** → 服务器上 commit + push 回 GitHub
5. **本地同步**：`git pull` 保持本地与服务器一致

**CI/CD 自动化**：
- 每次 commit 后自动触发 GitHub Actions
- 检测：版本管理、commit 树、服务器实测、单测
- 失败自动回滚

**安全协议**（延续）：

**上线的服务要严守网络安全底线**：
- 所有写操作必须有鉴权（已实现：API Key，写操作需要 `X-API-Key` 请求头）
- 所有密钥/Token 必须放在 `data/secrets/` 或环境变量，不进 git
- 定期审查端口开放（只开 80/443/22）
- 定期备份数据（每天凌晨 3 点）

## 关键经验（来自本次部署）

1. **SSH 用户是 `ubuntu` 不是 `root`**（腾讯云 Ubuntu 默认）
2. **FastAPI 需要 `python-multipart`**（处理文件上传）
3. **macOS tar 有扩展属性**，Linux 解压会报 `LIBARCHIVE.xattr` 警告（不影响功能）
4. **数据目录结构**：`data/birds.db` + `data/thumbs/`，打包时分开传（代码 63KB，数据 37MB）
5. **Nginx 反向代理**：`proxy_pass http://127.0.0.1:8765`，超时时间要设长（300s）
6. **Linux 权限链**：`www-data` 要读 `/home/ubuntu/...`，每一级目录都要 `chmod 755`
7. **百度地图瓦片免 key**：`maponline{s}.bdimg.com/tile/?qt=tile&x={x}&y={y}&z={z}&styles=pl&scaler=1`

## 参考文档
- 完整方案：`MVP开发方案.md`（含 DDL、模块规格、P0–P4 里程碑、风险清单）
- 使用说明：`README.md`
- 部署指南：`docs/腾讯云部署指南.md`
- 部署 SOP：`.workbuddy/skills/birding-deploy/SKILL.md`
