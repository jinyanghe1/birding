# 家庭共享相册 SPEC（类 Apple Shared Library）

> 版本 v1.0 · 2026-08-30 · 状态：待评审，未实现

## 1. 目标与范围

把当前「单人观鸟数据库」升级为「家庭共享观鸟平台」，对标 Apple Photos 的
Shared Library / Shared Albums：

| 能力 | 现状 | 目标 |
|---|---|---|
| 上传 | 仅本地 localhost 手动导入 | 指定用户（家人）经公网网页上传，手机可直接从相册批量选图 |
| 下载 | 无 | 一键下载整本相册（zip），或单张原图 |
| 多用户 | 无用户概念 | 邀请制用户体系，按用户隔离相册 |
| 手机 | 无适配 | 响应式 + PWA，支持添加到主屏 |
| 相册 | 无 | 按用户/主题分类相册 |

**明确不做**（v1）：
- 开放注册（只支持管理员邀请）
- 社交功能（点赞/评论）
- 端到端加密（家庭场景，服务器可信）
- 视频大文件分片续传（>200MB 才需要，v2 再做）

## 2. 用户模型

- **角色**：`owner`（我，全部权限）/ `member`（家人，上传/查看自己的相册）
- **认证**：延续现有 API Key 方案，每用户一把 key：
  - `POST /api/auth/login` 用 key 换 session token（JWT，7 天有效）
  - 写操作要求 `Authorization: Bearer <token>` 或 `X-API-Key`
- **邀请流程**：owner 在管理页生成邀请链接（含一次性邀请码）→ 家人打开填
  昵称 → 系统生成该用户的 API Key → 家人把 key 存到手机浏览器 localStorage

## 3. 数据模型（新增 3 表，不影响现有 6 表）

```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,     -- 登录名
  display_name TEXT NOT NULL,        -- 显示名（"妈妈"）
  api_key TEXT UNIQUE NOT NULL,      -- 32 hex，写操作鉴权
  role TEXT NOT NULL DEFAULT 'member', -- owner | member
  invite_code TEXT,                  -- 一次性邀请码（可空）
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE albums (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,                -- "深圳湾 2026 春"
  description TEXT,
  cover_photo_id INTEGER,            -- 封面
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE cloud_photos (
  id INTEGER PRIMARY KEY,
  album_id INTEGER NOT NULL REFERENCES albums(id),
  uploader_id INTEGER NOT NULL REFERENCES users(id),
  filename TEXT NOT NULL,            -- 服务器文件名（uuid.jpg）
  orig_name TEXT,                    -- 原始文件名
  size_bytes INTEGER,
  width INTEGER, height INTEGER,
  shot_at TEXT,                      -- EXIF 拍摄时间
  media_type TEXT,                   -- image | video
  exif_json TEXT,                    -- 原始 EXIF（含 GPS）
  species_id INTEGER,                -- 识别出的鸟种（可空，先传后识别）
  thumb_path TEXT,                   -- 缩略图相对路径
  orig_path TEXT,                    -- 原图相对路径
  sync_status TEXT DEFAULT 'local_only', -- local_only | uploaded | synced
  created_at TEXT DEFAULT (datetime('now'))
);
```

**与现有表的关系**：cloud_photos 里的照片可进识别流水线——识别通过后
写入 observations/photos（`image_source='cloud'`），从而合并进物种墙与地图。

## 4. API 接口规划（v1 全部 11 个）

### 认证
| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/auth/login` | 无 | body `{api_key}` → `{token, user}` |
| POST | `/api/auth/invite` | owner | 生成邀请码，返回邀请链接 |
| POST | `/api/auth/register` | 邀请码 | `{invite_code, display_name}` → 返回新用户 API Key（仅一次） |

### 相册
| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/albums` | 登录 | 列出当前用户可见相册（owner 看全部，member 看自己的） |
| POST | `/api/albums` | 登录 | 创建相册 `{name, description}` |

### 照片（上传/下载/同步）
| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/albums/{id}/photos` | 登录 | 批量上传（multipart 多文件），返回每张的识别状态 |
| GET | `/api/albums/{id}/photos` | 登录 | 分页列出照片（缩略图 URL + 元数据） |
| GET | `/api/photos/{pid}` | 登录 | 单张原图（Content-Disposition 内联） |
| GET | `/api/photos/{pid}/thumb` | 登录 | 缩略图 |
| GET | `/api/albums/{id}/download` | 登录 | 整本相册打包 zip（后台任务 + 轮询下载地址） |
| DELETE | `/api/photos/{pid}` | 上传者或 owner | 删除单张 |

### 本地↔云端同步（双向传递核心）
| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/sync/pull` | 登录 | 列出云端相册里我还没有的照片清单（按 `sync_status` 过滤） |
| POST | `/api/sync/push` | 登录 | 上传照片并标记 `sync_status='uploaded'`，本地脚本拉取后标 `synced` |

**本地侧**：新增 `bird sync pull <album>` / `bird sync push <album>` CLI：
- pull：从服务器下载新照片到本地目录，可选自动导入 Photos
- push：把本地目录新照片批量上传到指定相册

## 5. 手机适配

1. **响应式**：相册页网格 `grid-template-columns: repeat(auto-fill, minmax(96px, 1fr))`，
   触摸友好的大按钮（min 44×44pt）
2. **批量选图**：`<input type="file" accept="image/*,video/*" multiple>` ——
   iOS/Android 浏览器均直接弹系统相册，支持多选
3. **PWA**：`manifest.json`（name=观鸟家庭相册，icons 192/512）+ service worker
   缓存静态资源 → 添加到主屏，体验接近原生 App
4. **上传进度**：XHR `upload.onprogress` 逐张显示进度条

## 6. 存储与容量评估

| 项 | 估算 |
|---|---|
| 单张原图 | 2–8 MB（iPhone 主流机型） |
| 服务器磁盘 | 40GB，系统占用 ~7GB，可用 ~33GB → 约 4000–16000 张 |
| 缩略图 | 服务器生成 512px webp，~50KB/张，几乎可忽略 |
| 带宽 | 上传 1 张 5MB ≈ 5MB 流量；腾讯云轻量按流量计费 |

**风险与对策**：
- 磁盘满 → 先删 `orig_path` 只留缩略图（可配置），或后续挂腾讯云 COS
- zip 打包内存 → 流式 zip（zipstream），不落盘
- 并发上传 → FastAPI 单进程即可（家庭并发 <10），SQLite 写锁用 WAL

## 7. 可行性评估（结论：可行，低风险）

| 维度 | 评估 |
|---|---|
| 技术 | 全部用现有栈（FastAPI + SQLite + 原生 JS），无新依赖，手机端无需原生开发 |
| 成本 | 0 新增成本；磁盘和带宽都在现有 ¥24/月服务器额度内 |
| 风险 | 低。最大风险是磁盘增长，对策见 §6 |
| 工期 | P5 分三期：P5-1 用户+相册（1天）→ P5-2 上传下载+手机适配（1.5天）→ P5-3 双向同步+PWA（1.5天） |

## 8. 里程碑（新增 P5，追加到 roadmap）

### P5-1 — 用户体系与相册（1 天）
- users/albums/cloud_photos 三表 + 迁移
- 认证（login / invite / register）+ 现有写操作接入用户鉴权
- 相册 CRUD + 管理页（owner 生成邀请链接）

### P5-2 — 上传下载与手机适配（1.5 天）
- 批量上传（multipart + 缩略图生成 + EXIF 解析）
- 相册页响应式网格 + 批量选图 + 上传进度
- 单张/zip 下载

### P5-3 — 双向同步与 PWA（1.5 天）
- `bird sync pull/push` CLI
- 云端照片进识别流水线（识别后并入物种墙）
- PWA manifest + service worker

## 9. 安全协议（延续长期记忆）

- 每用户独立 API Key，写操作必须鉴权
- 邀请码一次性，注册后立即失效
- Key 只存 `data/secrets/`，不进 git、不返回前端源码
- 删除是软删除（`sync_status='deleted'`）还是硬删除？v1 用硬删除但要求二次确认
