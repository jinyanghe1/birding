# 本地观鸟数据库

> 扫描 macOS Photos 中的鸟类照片/视频，自动识别并维护「鸟种 / 图片 / 时间 / 地点 / 次数」档案。支持 localhost 网页浏览 + 静态站点导出（GitHub Pages）。

## 快速开始

```bash
# 初始化
python -m birdscan.cli init

# 扫描照片库
python -m birdscan.cli scan

# 本地模型自动识别
python -m birdscan.cli auto

# 准备网页全图（只处理本地文件，不批量下载 iCloud 原图）
python -m birdscan.cli fullsize

# 启动网页
python -m birdscan.cli serve

# 导出静态站点（GitHub Pages 用）
python -m birdscan.cli export-site
```

## 手动导入

网页右上角「加新」→ 上传图片或视频 → 自动识别鸟种 → 入库。

## 全图预览

物种墙和详情列表继续使用 320px 缩略图；点击详情照片中的「预览全图」才加载 `data/full/` 里的独立文件。Mac 本地 JPEG/MPO 原图逐字节复制，HEIC/PNG 等仅转换格式、不降低分辨率；iCloud-only 照片使用已有最清晰衍生图，并在预览底部明确标注。视频展示已抽取的帧。`scripts/scan_daily.sh` 会增量生成并同步全图，不会在 launchd 中触发 iCloud 下载。

## 架构

```
Photos 库 -> osxphotos -> MegaDetector -> 连拍去重 -> ConvNeXt 分类 -> SQLite
                                              |
                                              v
                                     localhost:8765 + 静态导出
```

## 技术栈

- Python 3.13 + osxphotos + ultralytics + onnxruntime
- FastAPI + Leaflet + 单页 HTML
- SQLite
