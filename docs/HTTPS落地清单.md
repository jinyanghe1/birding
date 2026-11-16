# HTTPS 落地清单（当前阻塞：无域名）

> 现状（2026-11-16）：服务器 `124.223.171.149` 只有 HTTP:80，nginx → FastAPI:8765。
> 写操作需要 `X-API-Key`，该 key 目前**明文经过公网**。HTTPS 是把它关住的唯一办法。
> 卡点：账号下**没有任何域名**，且实例在 ap-shanghai（中国大陆），对外提供 Web 服务必须备案。

## 一、两条路线（二选一）

| 路线 | 做法 | 时间/成本 | 适合 |
|---|---|---|---|
| **A. 买域名 + 备案**（推荐） | 腾讯云买域名 → 备案（个人主体，免费）→ 免费 SSL 证书 → 配 nginx 443 | 备案 1–3 周，域名 ~30–60 元/年 | 长期对外、给家人用、想要 `birds.xxx.com` |
| B. 迁到香港/新加坡地域实例 | 新开免备案实例，迁移部署 | 新购套餐 + 迁移半天 | 只要 HTTPS 不想备案 |

> 备案期间域名无法解析到大陆 IP 提供 Web 服务（80/443 会被拦），所以 A 路线的顺序必须是
> **先备案通过，再配 nginx**。备案期间可以先用 `https://124.223.171.149` 的自签证书做内部测试，
> 但浏览器会告警，不建议给家人用。

## 二、备案通过后的执行步骤（约 20 分钟）

1. **解析**：控制台加 A 记录 → `birding.你的域名` → `124.223.171.149`
2. **放行 443**：Lighthouse 控制台防火墙加规则 `TCP:443`（当前只放行 22/80/ICMP）
   —— 或让我用 MCP 工具加
3. **申请证书**：腾讯云 SSL 证书控制台 → 免费 DV 证书（90 天）→ 下载 nginx 版
   - 免费证书到期需手动续，建议用 acme.sh 自动续期（Let's Encrypt，免人工）
4. **服务器 nginx 配置**（`/etc/nginx/sites-available/birding`）：

```nginx
server {
    listen 80;
    server_name birding.你的域名;
    return 301 https://$host$request_uri;      # HTTP 全量跳 HTTPS
}

server {
    listen 443 ssl http2;
    server_name birding.你的域名;

    ssl_certificate     /etc/nginx/ssl/birding.crt;
    ssl_certificate_key /etc/nginx/ssl/birding.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_session_cache   shared:SSL:10m;

    add_header Strict-Transport-Security "max-age=31536000" always;

    client_max_body_size 60m;                   # 上传照片用
    root /var/www/html;
    index index.html;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
    }
}
```

5. **重载 + 验证**：`sudo nginx -t && sudo systemctl reload nginx`，
   然后 `curl -I https://birding.你的域名` 应 200，`http://` 应 301
6. **收尾**：
   - 前端/部署脚本里的 `http://124.223.171.149` 全部替换成域名（`scripts/deploy_to_server.sh`、
     `tests/e2e_test.py` 的 `BIRDING_BASE`、以及记忆里的访问地址）
   - 证书续期加进 crontab 或 systemd timer，并把「到期前 15 天」加进监控

## 三、如果决定不上 HTTPS

那至少做这两件事降低风险（都不需要域名）：
1. 写操作用的 API Key 定期轮换（现在存 `data/.api_key` + 服务器 systemd drop-in）
2. 不在公共 Wi-Fi 下做上传/删除类写操作

## 四、判定记录

- 2026-11-16：`describe_domains` 返回 0 条 → 无域名，HTTPS 无法开始。
- 结论：需用户先决定「买域名+备案」还是「换免备案地域」，我再执行技术侧动作。
