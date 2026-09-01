# 家庭共享相册 v2 SPEC（Apple Photos 逻辑）

> 版本 v2.0 · 2026-09-01 · 状态：待开发
> 变更：相册与邀请码解耦 + 相册二级 tab + 图片去重

## 1. 核心变更

### v1 → v2 对比

| 维度 | v1 | v2 |
|---|---|---|
| 相册创建 | 直接创建，无邀请码 | 创建相册 → 生成邀请码（绑定相册权限） |
| 邀请码 | 全局一把，注册后固定权限 | 每个邀请码绑定相册列表，可勾选 |
| 相册位置 | 独立 tab「家庭相册」 | 物种墙下的二级 tab（全部/单个相册） |
| 图片存储 | 每张照片存一份 | 多相册去重（哈希），不同相册共享同一文件 |
| 上传目标 | 固定到当前相册 | 可选相册（默认「物种墙」） |

## 2. 数据模型（v2）

```sql
-- 相册表（不变）
CREATE TABLE albums (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  description TEXT,
  cover_photo_id INTEGER,
  is_default BOOLEAN DEFAULT 0,        -- 是否是默认相册（物种墙）
  created_at TEXT DEFAULT (datetime('now'))
);

-- 相册-照片关联表（多对多，去重核心）
CREATE TABLE album_photos (
  id INTEGER PRIMARY KEY,
  album_id INTEGER NOT NULL REFERENCES albums(id),
  photo_id INTEGER NOT NULL,           -- 指向 cloud_photos.id
  added_at TEXT DEFAULT (datetime('now')),
  UNIQUE(album_id, photo_id)           -- 同一照片在同一相册只出现一次
);

-- 云端照片表（v2 改）
CREATE TABLE cloud_photos (
  id INTEGER PRIMARY KEY,
  uploader_id INTEGER NOT NULL REFERENCES users(id),
  filename TEXT NOT NULL,
  orig_name TEXT,
  file_hash TEXT UNIQUE NOT NULL,      -- SHA256，去重核心
  size_bytes INTEGER,
  width INTEGER, height INTEGER,
  shot_at TEXT,
  media_type TEXT,
  exif_json TEXT,
  species_id INTEGER,
  thumb_path TEXT,
  orig_path TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

-- 邀请码表（v2 改：绑定相册权限）
CREATE TABLE invites (
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,           -- 8 hex
  created_by INTEGER NOT NULL REFERENCES users(id),
  album_ids TEXT NOT NULL,             -- JSON 数组 [1, 2, 3]
  max_uses INTEGER DEFAULT 1,          -- 最大使用次数
  used_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  expires_at TEXT                      -- 过期时间（可空）
);
```

## 3. API 接口规划（v2 全部 15 个）

### 相册管理
| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/albums` | 登录 | 列出当前用户可见相册（owner 全部，member 被邀请的） |
| POST | `/api/albums` | 登录 | 创建相册 `{name, description}` |
| GET | `/api/albums/{id}` | 登录 | 相册详情 + 照片列表 |
| POST | `/api/albums/{id}/photos` | 登录 | 批量上传（自动去重） |
| DELETE | `/api/albums/{id}` | owner | 删除相册 |

### 邀请码
| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/invites` | owner | 生成邀请码 `{album_ids: [1,2], max_uses: 1}` |
| GET | `/api/invites/{code}` | 无 | 验证邀请码有效性（不消耗） |
| POST | `/api/invites/{code}/use` | 无 | 使用邀请码注册 `{display_name}` |

### 照片（去重核心）
| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/photos/{pid}` | 登录 | 单张原图 |
| GET | `/api/photos/{pid}/thumb` | 登录 | 缩略图 |
| GET | `/api/photos/hash/{hash}` | 登录 | 按哈希查照片（用于去重检查） |
| POST | `/api/photos/{pid}/albums` | 登录 | 把照片添加到其他相册 |
| DELETE | `/api/photos/{pid}` | 上传者或 owner | 删除（从所有相册移除） |

### 同步
| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/sync/pull` | 登录 | 列出云端照片清单 |
| POST | `/api/sync/push` | 登录 | 标记同步状态 |

## 4. 前端逻辑（Apple Photos 风格）

### 相册 tab 结构
```
物种墙
├── 全部（默认，聚合所有相册）
├── 深圳湾 2026 春
├── 妈妈拍的鸟
└── + 新建相册
```

### 上传流程
1. 点「批量上传」→ 选择照片
2. 前端计算每张的 SHA256
3. 先调 `GET /api/photos/hash/{hash}` 检查是否已存在
4. 已存在 → 提示「已存在，是否添加到当前相册？」→ `POST /api/photos/{pid}/albums`
5. 不存在 → 正常上传 `POST /api/albums/{id}/photos`

### 邀请流程
1. owner 点「邀请家人」
2. 弹窗勾选相册权限（默认全选）
3. 生成邀请码 → 复制链接
4. 家人打开链接 → 填名字 → 自动加入勾选的相册

## 5. 去重逻辑

```python
# 上传时
file_hash = hashlib.sha256(raw).hexdigest()
existing = get_photo_by_hash(file_hash)
if existing:
    # 已存在：只加关联，不重复存储
    add_to_album(existing.id, album_id)
    return {"ok": True, "id": existing.id, "duplicate": True}
else:
    # 不存在：存原图 + 缩略图
    save_photo(raw, file_hash)
    return {"ok": True, "id": new_id, "duplicate": False}
```

## 6. 远期 roadmap（不实现）

- 相册 filter（全部/单独相册/按人筛选）
- 相册共享链接（只读，不需要注册）
- 相册封面自定义
- 相册内搜索

## 7. 验收标准

- [ ] 创建相册成功，前端显示新相册
- [ ] 邀请码生成时可选相册权限
- [ ] 家人注册后只能看到被邀请的相册
- [ ] 同一照片上传到两个相册，只存一份文件
- [ ] 相册 tab 切换正常（全部/单个）
- [ ] 上传时自动去重（哈希检查）
