# 观鸟 Skill 项目长期笔记

## 项目目标
本地观鸟数据库（"本地懂鸟"）：扫描 macOS Photos → 自动发现新增鸟类照片/视频 → 去重 → 挑清晰图 → 识别鸟种 → 入库（鸟种/图片/时间/地点/次数）→ localhost 网页浏览 + 封装成 Agent Skill。双端（Web + Skill）共用同一个 SQLite。

## 硬约束（来自本机实测，改方案前必读）
1. **只能用本地缩略图 `p.path_derivatives`**，绝不能调用 `export(use_photos_export=True)` —— 89% 原图在 iCloud，强行下载会在 launchd 无 GUI 会话下卡死。
2. **连拍分组必须自建**：Photos 的 `burst` 标记实际只有 3/15,413 张，等于没有。用「时间戳间隔 ≤2s 分组 + pHash 汉明距离 ≤6 去重」。
3. **不能依赖 Photos 的系统标签**：`labels_normalized` 全为 0。
4. **DB schema 是 `ZASSET`**（不是老教程里的 `ZGENERICASSET`），裸写 SQL 时注意。
5. **定时任务用 launchd，不用 cron**（cron 在 macOS 无 TCC 归属）。

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

## 安全协议（长期记忆，上线服务必守）

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
