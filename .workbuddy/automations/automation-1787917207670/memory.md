# 每日观鸟照片扫描 — 执行记录

## 2026-08-28 21:00
- 首次执行（此前无 memory 文件）。
- 扫描：照片库 15,416 张，新增 1 张（`4EF61E14-3D62-45C5-912A-BF2AF561DAD7`）。
- L1 MegaDetector 未检出动物（animal_conf=0.0，stage=`l1_no_animal`），L2 保留 0。
- 未运行 `auto`（无候选图，符合预期）。本次新增鸟种 0、新增观测 0。
- 库存量：鸟种 380 / 观测 785 / 照片 2,214 / 已扫资产 15,416；待识别队列 0；置信度 <0.45 待复核 589 条。

### 经验
- `scan` 输出末尾的 JSON 摘要（`new_assets/l1_passed/l2_kept/new_species`）是最快的判定依据。
- `new_assets` 字段是**库内资产总数**而非本次新增数；真正的新增数看日志里的「待检测 N」行。
- 仅当 `l2_kept > 0` 时才需要跑 `auto`。
- 待复核（<0.45）基数较大（589），每次报告带上该数即可，无需重扫全库。

## 2026-08-29 21:00
- 第 2 次执行。照片库 15,483（较上次 +67），本次 scan 仅 1 张新增，L1 未检出动物，l2_kept=0。
- **但发现 20:11 那次后台扫描遗留 36 条 id_queue pending**，补跑 `auto` 处理完毕（bird 33 / no_bird 3 / accepted 18 / needs_review 15）。
- 产出：新增观测 5 条、新增鸟种 4 种；另 28 条并入 08-28 已有观测。待复核(<0.45) 439→443。
- 库存量：鸟种 384 / 观测 510 / 照片 2,251 / 已扫资产 15,508；待识别队列 0。
- 误识别预警：凤头距翅麦鸡(南美)、小凤头鹦鹉(澳洲)、虎皮鹦鹉(澳洲,conf 0.691 需人工看)、小葵花鹦鹉。
- 全库 <0.45 的 443 条中有 275 条 in_china=0，建议优先批量清理。

### 经验（重要，覆盖上次）
- **`l2_kept=0` 不等于无需跑 `auto`** —— 必须先查 `SELECT COUNT(*) FROM id_queue WHERE status='pending'`。
  上次按「仅当 l2_kept>0 才跑 auto」的规则执行，导致 36 条积压一整天。
- `auto` 按「物种+日期+地点」合并写入，33 条 bird 只产出 5 条新观测。报数要用
  「新观测 N / 新鸟种 M / 并入旧观测 K」三段式，并反查 `id_queue.asset_uuid → photos.obs_id`。
- 表名/列名备忘：无 `assets` 表（是 `scanned_assets`）；物种中文名是
  `species.common_name_cn`，不是 `common_name`；`in_china`：-1 未知 / 0 否 / 1 是。
- 观察：`new_assets` 字段仍是库内总数，真正新增数看日志「待检测 N」行。

## 2026-08-30 21:00
- 第 3 次执行。照片库 15,485（+2），L0 待检测 2，L1 均未检出动物，l2_kept=0。
- 先查 `id_queue pending = 0`，按上次教训确认无积压，**正确地未跑 `auto`**。
- 新增鸟种 0 / 新增观测 0。库存：鸟种 384 / 观测 510 / 照片 2,251 / 已扫 15,510。
- 待复核（<0.45）443 条（in_china=0 的 275 / =1 的 165 / 未知 3），与上次持平，无变化。

### 口径修正（重要，覆盖 08-28 记录）
- **置信度列是 `observations.confidence`**。此前记录写的 `photos.confidence` 是错的
  （photos 表无该列），`id_queue.result_conf` 也不存在 —— id_queue 的置信度在
  `result_json` 的 JSON 串里。统计全库待复核数一律用 observations 表（观测粒度）。
- 判断顺序固化为：scan → 查 `id_queue pending`（非看 l2_kept）→ 有积压才跑 auto → 查 observations 报数。

## 2026-09-01 21:00
- 第 4 次执行。照片库 15,572（+87），L0 待检测 87 → L1 通过 40 → L2 保留 27（本次确有新增）。
- `id_queue pending = 27`，跑 `auto`：bird 23 / no_bird 4 / accepted 3 / needs_review 20。
- 产出：新增观测 6、新增鸟种 3（海鸽 Pigeon Guillemot / 棕贼鸥 Brown Skua /
  白腹籽鹬 White-bellied Seedsnipe），无并入旧观测。
- 库存：鸟种 387 / 观测 516 / 照片 2,278 / 已扫 15,597；待复核(<0.45) 443→448
  （in_china=0 的 276 / =1 的 166 / 未知 6）。队列已清空。
- **误识别预警**：6 个物种里 4 个非中国分布 —— 白腹鹳(Abdim's Stork,非洲,0.017)、
  海鸽(北美,0.069)、白腹籽鹬(南美安第斯,0.055)、棕贼鸥(南极,0.270)。
  唯一可信：金斑鸻(Pacific Golden Plover, 0.934)。

### 经验（新增）
- **`result_json` 的正确字段名**：`common_name_cn` / `common_name_en` /
  `confidence` / `is_bird` / `notes`（不是 species、verdict、score）。
- **新鸟种判定用 `species.created_at`**，不要只用「总数差」——本次总数 +3，
  但白腹鹳是已有物种（in_china=0），靠总数差会误判。
- **photos 表在 scan 阶段就已写入**（L2 保留即入库），所以 photo 数的基线要取
  scan 之后、auto 之前；auto 只负责写 observations，不再动 photos。
- 检测框 conf 高（0.94）≠ 分类 conf 高。二者分列 `notes` 与 `confidence`，
  出现「框准、种错」时不要误报为好结果。
