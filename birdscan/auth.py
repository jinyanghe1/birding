"""轻量级鉴权：API Key + Cookie 登录态。

方案：
  1. API Key（推荐）：请求头 X-API-Key，本地和服务器各配一个
  2. Cookie 登录态：登录后种 HttpOnly Cookie（birding_key），
     供 <img>/window.open 等无法带自定义 header 的场景使用

配置：
  - 本地：config.LOCAL_API_KEY（默认 dev-key，不需要改）
  - 服务器：环境变量 BIRDING_API_KEY 或 data/.api_key 文件
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Header, Request

from . import config

COOKIE_NAME = "birding_key"


def _get_api_key() -> str:
    """从环境变量或文件读 API key。"""
    # 1. 环境变量（生产环境）
    key = os.environ.get("BIRDING_API_KEY")
    if key:
        return key
    # 2. 文件（本地开发）
    key_file = Path(config.DATA_DIR) / ".api_key"
    if key_file.exists():
        return key_file.read_text().strip()
    # 3. 默认（开发环境）
    return "dev-key"


API_KEY = _get_api_key()

# 写操作需要鉴权的端点
WRITE_ENDPOINTS = {
    "/api/photo", "/api/observation", "/api/review/not-bird",
    "/api/review/reassign", "/api/upload", "/api/scan",
}


def key_from_request(request: Request | None, x_api_key: str | None = None) -> str:
    """Header 优先，其次 Cookie（<img>/window.open 场景）。"""
    if x_api_key:
        return x_api_key
    if request is not None:
        return request.headers.get("X-API-Key") or request.cookies.get(COOKIE_NAME, "")
    return ""


def verify_api_key(x_api_key: str = Header(None), request: Request = None):
    """FastAPI 依赖：验证 API key。

    用法：
        @app.delete("/api/photo/{uuid}")
        def delete_photo(uuid: str, _: None = Depends(verify_api_key)):
            ...
    """
    # 读操作不需要鉴权
    if request and not any(request.url.path.startswith(ep) for ep in WRITE_ENDPOINTS):
        return

    key = key_from_request(request, x_api_key)
    if not key or not secrets.compare_digest(key, API_KEY):
        raise HTTPException(status_code=401, detail="未授权")


# 可选：IP 白名单（增强安全）
IP_WHITELIST = set(os.environ.get("BIRDING_IP_WHITELIST", "").split(","))


def verify_ip(request: Request):
    """FastAPI 依赖：验证 IP 白名单。"""
    if not IP_WHITELIST:
        return  # 未配置白名单，跳过
    client_ip = request.client.host
    if client_ip not in IP_WHITELIST:
        raise HTTPException(status_code=403, detail=f"IP {client_ip} 不在白名单")
