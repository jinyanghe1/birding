---
name: birding
description: |
  本地观鸟数据库。当用户聊到以下话题时使用：
  - 我看过哪些鸟 / 拍过哪些鸟 / 观鸟记录
  - 某只鸟在哪里拍的、什么时候拍的、见过几次
  - 今年加新了几种 / 累计多少种
  - 扫描新增照片里的鸟 / 继续识别鸟的照片
  - 帮我复核低置信度的识别结果
  - 打开观鸟档案网页
  不要回答关于识别技术实现的问题；只回答数据层与交互。
version: 0.1.0
author: WorkBuddy
---

# 本地观鸟数据库（birding skill）

## 数据定位

- 数据库和 Web 服务共享一个 SQLite，位于 `/Users/hejinyang/WorkBuddy/观鸟skill/data/birds.db`。
- 你是数据的使用者，不是唯一的拥有者。命令输出和网页永远读取同一个事实来源。
- 不要在你的回复中维护一份「我记住的鸟种清单」，除非只是为了本次回答方便；需要精确数据时始终调 CLI。

## 调用方式

通过 CLI `bird`（venv 里的 Python 模块）访问：

```bash
python -m birdscan.cli <command> [options]
```

venv 路径：`/Users/hejinyang/.workbuddy/binaries/python/envs/birdskill/bin/python`

## 能力清单

| 意图 | 命令 | 说明 |
|---|---|---|
| 概览统计 | `bird stats --json` | 种数、次数、照片数、地点数、待识别数、上次扫描结果 |
| 我看过哪些鸟 | `bird list --order count --json` | 按观测次数排序的鸟种列表 |
| 查看某鸟详情 | `bird show "红嘴蓝鹊" --json` | 中文名/学名/次数/时间地点/照片路径 |
| 搜索 | `bird list --search "鹊" --json` | 中文名/学名/科名模糊搜索 |
| 地点排行 | `bird places --limit 10` | 观测次数最多的地点 |
| 低置信度复核 | `bird review --threshold 0.55` | 列出需要人工确认的识别 |
| 取出待识别照片 | `bird queue --take 20 --prompt` | 生成带时间地点先验的 Agent 识别提示词 |
| 回写识别结果 | `bird apply results.json` | 用户把识别结果写进 JSON 文件后批量入库 |
| 手动扫描 | `bird scan --limit 500` | 扫描照片库，产出识别队列（可定时自动跑） |
| 自动识别 | `bird auto` | 本地 ONNX 模型批量识别队列 |
| 补全元数据 | `bird enrich` | 名录匹配 + 科属 + 维基摘要 + eBird/BOW/Xeno-canto 外链 |
| 数据维护 | `bird merge-obs --infer-places` | 合并拆散观测 + 地点时间邻近推断 |
| 启动网页 | `bird serve` | 本地 http://127.0.0.1:8765（含交互地图） |

## 与用户互动的标准流程

### 1. 回答「我看过哪些鸟」

1. 执行 `bird list --order count --json`。
2. 用自然语言总结：
   - 总种数、总观测次数；
   - Top 5 常见种（次数）；
   - 最近 3 次观测；
   - 附上网页入口：`http://localhost:8765`。
3. 如果列表为空，提示「还没有跑过扫描」，询问是否现在扫。

### 2. 识别新增照片

1. 先问用户：是否只处理最近 N 天？是否现在就开始识别？
2. 执行 `bird queue --take 20 --prompt` 拿到提示词与图片路径。
3. 你作为视觉模型，查看这些图片，输出结构化的 JSON 数组：

```json
[
  {"asset_uuid":"...","is_bird":true,"common_name_cn":"红嘴蓝鹊",
   "scientific_name":"Urocissa erythroryncha","confidence":0.92,
   "notes":"尾羽极长、喙与脚红色、头颈部黑白相间"}
]
```

4. 把 JSON 写入 `/tmp/bird_results_*.json`，执行 `bird apply <file>` 回写数据库。
5. 重复直到队列为空或用户说停。

**重要**：必须利用提示词里已经给出的「拍摄时间 + 地点」做时空先验，缩小候选集（例如 11 月在上海的「黑白色大型水鸟」优先想到东方白鹳/黑脸琵鹭，而不是雪鸮）。

### 3. 复核低置信度

1. 执行 `bird review --threshold 0.55`。
2. 对列出的每条记录，查看图片，确认或修正鸟种。
3. 如果修正，执行 `bird apply` 或直接告诉用户去网页端点编辑（后续版本）。

## 输出风格

- 用中文，简洁结构化，TL;DR 在前。
- 物种名优先中文名，括号备注学名。
- 不要编造数据；没有的数据明确说「未记录」。
