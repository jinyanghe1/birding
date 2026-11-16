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

## 数据质量：置信度阈值不可靠，必须逐张看图（2026-11-16 实测修正）

- `species.in_china` 取值：-1 未知 / 0 非中国 / 1 中国。查询用它做筛选维度可以，
  **但不能按阈值批量删数据**——2026-11-16 抽检证伪：
  - `鹊鸲 conf=0.11` → 图里真是鹊鸲（低置信=真鸟）
  - `加拿大黑雁 conf=0.41` → 识别正确
  - `南美绿鹭 conf=0.03` → 是猪圈照片；`阿德利企鹅 conf=0.62` → 是人民大会堂
  → 低置信里混真鸟、高置信里混建筑，阈值法两头都错。
- **复核工具**：`scripts/review_batch.py`
  - `export --bucket {high_conf_foreign|mid|low|all_foreign} [--limit N]` → 导出批次（默认按物种去重，每物种一张代表图）
  - `apply --file decisions.json [--dry-run]`，decision 取值 `not_bird`（删）/`reassign`（改判）/`keep`（保留，可带 `note`）
  - `store.annotate_observation(obs_id, note)` 把复核结论写进 `notes`，**不删数据不改物种**
  - 判定原则：非鸟→删；鉴定明确错误→改判；**真鸟但是笼养/园养/宠物 → keep + note（动物园拍的也是真观测）**
- 当前状态（2026-11-16 首批复核后）：**474 种 / 660 观测 / 1201 照片**；
  高置信(≥0.45) 89 条 / 44 种；待复核 591 条（大多是 <0.20 噪声，夹着真鸟，需逐张看）。
- 首批 16 条高置信误识别已处理：2 删（人民大会堂、公园湖景）、2 改判
  （黑颈长脚鹬→黑翅长脚鹬、粉头斑鸠→灰斑鸠）、12 保留并标注（8 条动物园/笼养 + 4 条物种存疑）。
- 剩余高置信但 in_china=0：**20 条**，是下一批目标。

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
4. **置信度 ≥0.45 才可信**（`config.UI_MIN_CONF`）。但见上节：阈值只能做筛选，
   不能做删除依据（<0.20 里也有真鸟）。界面默认过滤，复核靠 review_batch 逐张看图。
5. 若将来换成多模态模型识别，走 `bird queue --take N --prompt` → 看图 →
   `bird apply result.json`，接口已经留好，与本地模型并存不冲突。
6. **本会话（2026-11-16 起）具备读图能力**：可直接用 ViewImage 看 `data/thumbs/*.jpg`
   复核识别结果，450px 缩略图足够判断大类/明显误识别，细节存疑时看 `image_path` 原图。

## 定时任务
- **实际生效的是 launchd**（用户已在 GUI 手动 load `~/Library/LaunchAgents/com.hejinyang.birdscan.plist`）；
  旧的 WorkBuddy automation `automation-1787917207670` 已不存在。
- 2026-11-15 修复：plist 历史上直接跑 `birdscan/cli.py` 导致 09-29 起每天 ImportError；
  现 cli.py 已硬化支持直接运行 + plist 改用 `-m birdscan.cli` 双保险。
- **本沙箱内 launchctl bootstrap/print 均被拦截**（Bootstrap failed: 5），改 plist 后需用户在 GUI 终端手动重载。

## 部署架构（2026-11-15 更新：单用户回滚 + 每夜自动同步）

**生产环境**：腾讯云轻量应用服务器（124.223.171.149）
- **架构**：Nginx (80) → FastAPI (8765) → SQLite
- **systemd 服务**：`/etc/systemd/system/birding.service`（自动重启）
- **访问地址**：http://124.223.171.149
- **详细文档**：`docs/腾讯云部署指南.md`
- **模型**：**单用户**（2026-11-15 回滚家庭共享）：匿名读 + X-API-Key 写；
  users/albums/invites/groups/cloud_photos 等 6 表物理保留但代码不引用
- **同步**：**本地为准、单向推服务器**。每晚 21:00 launchd 跑
  `scripts/scan_daily.sh` 五步流水线：scan → 条件 auto → 本地快照（7份轮换）→
  rsync（密钥 **`~/.ssh/birding_deploy`**）→ 服务器停服清 WAL 原子换库重启
  （服务器备份轮换 7 份，DB 走 `birds.db.new` 暂存 + mv 原子换入）
- **deploy_to_server.sh [--data]** 仍可用于手动全量部署；`--data` 直接覆盖服务器 DB
  （回滚后无家庭数据顾虑，但注意会重置服务器备份文件名约定）
- **控制台信息（Lighthouse MCP 已连接，2026-08-31 确认）**：
  - InstanceId `lhins-3jodviz2`，名称 Ubuntu-1GSt，地域 **ap-shanghai**（不是北京）
  - 套餐 bundle_starter_mc_promo_med2_01：2 核 2GB / 40GB SSD / 3Mbps，包年包月
  - 到期 2027-08-29，续费方式 NOTIFY_AND_MANUAL_RENEW
  - 流量包：**200GB/月**（周期每月 29 日重置），实际用量 <130MB/月，无超额
  - 控制台防火墙：只放行 22(SSH)/80(HTTP)/ICMP，与安全协议一致；实例内 ufw 未启用（不重复开）
  - 服务器 thumbs 目录属主必须 **ubuntu**（曾误设 www-data 导致 rsync Permission denied）
  - **快照**：`lhsnap-nywf1kf6`（2026-11-16 建，系统盘 40G，状态 NORMAL）——控制台级备份已落地
  - **告警策略**（2026-11-16 建，绑定 lhins-3jodviz2）：CPU>85% 持续3周期(policy-05i85e3d)、
    内存>85%(policy-xl0u5zsa)、流量包>90%(policy-6r9d0sdv)，均 1 小时通知一次
  - **HTTPS 未落地**：账号下 0 个域名，大陆实例需先备案。落地清单见 `docs/HTTPS落地清单.md`

**launchd 定时任务**：plist 现直接执行 `scripts/scan_daily.sh`（含全部五步），
不再只跑 scan。**沙箱内 `launchctl bootstrap` 一律 Bootstrap error 5，只能用户在 GUI 终端装**：
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hejinyang.birdscan.plist`

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
- e2e 测试已改读 `BIRDING_TEST_KEY` 环境变量（P0-1 闭环）。

**服务器真实状态（SSH 必须带 `-i ~/Downloads/birding.pem`，沙箱不带 key 连不上）**：
- ~~服务器代码 = 2026-09-01 部署~~ **✅ 2026-11-15 已回滚为单用户版**（家庭共享下线，详见部署架构节）
- ~~⚠️ `deploy --data` 会抹掉服务器家庭数据~~ 回滚后无家庭数据，同步走 scan_daily.sh 流水线
- 观测数据：本地 516/387（10-02），服务器 509/384（08-29）。
- **服务器 venv 需 pillow**（10-14 补装；部署脚本不装依赖，环境漂移无感知——新增依赖要手动 ssh 装）。

**相册功能已复活（10-14）**：sharing_v2 补齐 4 函数、重建表迁移、邀请原子化、GC、上传白名单；
**Cookie 登录态**：`POST /api/auth/session` 种 HttpOnly cookie，`<img>`/`window.open` 靠它鉴权；
**前端结构**：`static/` = index.html + `css/app.css` + 10 个 `js/*.js` 模块（无构建）；
**测试**：`tests/test_api.py`（TestClient 8 用例，`TMPDIR` 需用干净目录绕沙箱 broker）+ `tests/e2e_test.py`（Playwright 7 用例，`BIRDING_BASE`/`BIRDING_TEST_KEY` 可配）。

**路线 C 重构已完成（10-14 晚，TDD 红绿 commit）**：
- 模型：`groups` 表 + `users/albums/invites.group_id` + `observations.owner_id`（存量回填 owner）；
  读范围三级（匿名=所有者公开数据 / member=仅自己 / owner=全部，`web._read_scope` 一行可改严格隔离）；
  邀请码绑定组（入组后组内相册全可见）；上传归属当前用户；写操作用户级鉴权+归属校验。
- Web 启动跑 `store.init_db()` + `sharing.init_sharing()`（schema 自愈，不依赖 CLI）。
- 测试：`tests/test_scoping.py` 8 + `tests/test_groups.py` 8 + test_api 8，共 24/24；生产 E2E 7/7。

**已修复的代码病灶**（详见 `docs/观鸟网站技术报告-2026-10-14.md` 文末执行记录）：
- ~~P0-2/P0-3/P0-4/P1-11~~ 全部已修；新发现：嵌套 conn_ctx 写锁 BUSY、服务器缺 pillow、
  api() headers 覆盖 bug、上传后延迟刷新竞态——均有回归测试覆盖。

**小程序路径结论**（`docs/小程序发布流程调研报告-2026-10-14.md`）：
多人互相上传=UGC→「社交-笔记」类目→**个人主体不开放，需个体户/企业主体**；
折中方案：仅 owner 上传+家人浏览→个人主体「工具-图片」类目可上线。域名+ICP+HTTPS 是硬前提。

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
