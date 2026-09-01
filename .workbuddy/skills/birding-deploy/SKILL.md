# 观鸟数据库部署与维护

本地观鸟数据库（macOS Photos → SQLite → FastAPI → 网页）的部署与维护 SOP。

## 触发场景

- 部署到腾讯云服务器
- 更新服务器上的代码/数据
- 排查服务故障
- 备份/恢复数据

## 前置条件

- 服务器：腾讯云轻量应用服务器（124.223.171.149）
- SSH 私钥：`~/Downloads/birding.pem`（权限 600）
- 本地项目：`/Users/hejinyang/WorkBuddy/观鸟skill`

## 部署流程

### 一键部署（推荐）

```bash
cd /Users/hejinyang/WorkBuddy/观鸟skill

# 只更新代码
./scripts/deploy_to_server.sh

# 更新代码 + 数据（37MB，需要几分钟）
./scripts/deploy_to_server.sh --data
```

### 手动部署

```bash
# 1. 打包代码
tar czf /tmp/birding.tar.gz --exclude='data' --exclude='models' --exclude='.git' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='logs' birdscan/

# 2. 上传
scp -i ~/Downloads/birding.pem /tmp/birding.tar.gz ubuntu@124.223.171.149:~/

# 3. 解压并重启
ssh -i ~/Downloads/birding.pem ubuntu@124.223.171.149
cd ~/birding && tar xzf ~/birding.tar.gz
sudo systemctl restart birding
```

## 服务管理

```bash
ssh -i ~/Downloads/birding.pem ubuntu@124.223.171.149

# 查看状态
sudo systemctl status birding

# 重启
sudo systemctl restart birding

# 查看日志
sudo journalctl -u birding -f

# 查看 Nginx 日志
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

## 故障排查

| 问题 | 排查 |
|---|---|
| 访问不了 | `sudo systemctl status birding` 看服务是否在跑 |
| 502 Bad Gateway | `sudo journalctl -u birding -n 50` 看报错 |
| 图片不显示 | `ls ~/birding/data/thumbs/` 看缩略图是否存在 |
| 数据库锁定 | `sudo systemctl restart birding` 重启服务 |

## 备份

```bash
# 手动备份
ssh -i ~/Downloads/birding.pem ubuntu@124.223.171.149
tar czf ~/backup/birding-$(date +%Y%m%d).tar.gz ~/birding/data/

# 自动备份（每天凌晨 3 点）
0 3 * * * tar czf ~/backup/birding-$(date +\%Y\%m\%d).tar.gz ~/birding/data/
```

## 安全建议

1. **防火墙**：只开放 80/443/22 端口
2. **HTTPS**：申请 Let's Encrypt 免费证书
3. **SSH**：禁用密码登录，只用密钥
4. **Secret**：Xeno-canto / eBird token 存在 `data/secrets/`，已加入 .gitignore

## 关键路径

| 路径 | 说明 |
|---|---|
| `~/birding/` | 项目根目录 |
| `~/birding/data/birds.db` | SQLite 数据库 |
| `~/birding/data/thumbs/` | 缩略图 |
| `/etc/systemd/system/birding.service` | systemd 服务配置 |
| `/etc/nginx/sites-available/birding` | Nginx 配置 |

## 常见错误

- **python-multipart 缺失**：`pip install python-multipart`
- **权限问题**：`sudo chown -R ubuntu:ubuntu ~/birding`
- **端口占用**：`sudo lsof -ti:8765 | xargs kill`
