# GitHub Pages 部署指南

仓库：<https://github.com/jinyanghe1/birding>（已推送）

## 一次性配置（GitHub 网页端）

1. 打开 <https://github.com/jinyanghe1/birding/settings/pages>
2. Source 选 **GitHub Actions**
3. 保存

## 本地部署流程（每次更新后）

```bash
# 1. 生成静态站点（含缩略图，不上传 git）
cd /Users/hejinyang/WorkBuddy/观鸟skill
python -m birdscan.cli export-site

# 2. 复制到 docs/（被 gitignore，只用于本地构建）
rm -rf docs
cp -r data/export_site docs

# 3. 加 index.html（把 /api/ 改为 ./api/，/img 改为 thumbs/）
#    见下方「静态版前端」
```

## 为什么这样设计

- **照片不上传 git**：隐私 + 体积（38MB 缩略图，原图更大）
- **数据不上传 git**：`data/birds.db` 在 .gitignore 里
- **模型不上传 git**：`models/` 在 .gitignore 里（YOLO + ConvNeXt 约 150MB）
- **静态导出**：GitHub Pages 只能托管静态文件，所以预先把数据库烘成 JSON
- **前端**：需要把 `birdscan/static/index.html` 里的 `/api/` 改成 `./api/`、`/img` 改成 `thumbs/`，才能脱离 FastAPI 独立运行

## 更新频率

- 照片识别每天 21:00 自动跑（WorkBuddy automation）
- 静态站点建议每周手动跑一次 `export-site` + 复制到 docs/ + 推 gh-pages 分支
