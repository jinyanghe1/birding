"""用浏览器自动化提交 eBird checklist。

前提：你需要先在浏览器里登录 eBird（只需一次，之后会话会保持）。
登录后这个脚本可以自动上传 CSV。

用法：
    python -m birdscan.cli ebird auto-submit

流程：
    1. 打开 https://ebird.org/import/upload.html
    2. 如果跳转到登录页，提示你手动登录
    3. 登录后自动上传 data/ebird_export.csv
    4. 提交后关闭浏览器
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from . import config

CSV = Path(config.DATA_DIR) / "ebird_export.csv"


def auto_submit() -> int:
    if not CSV.exists():
        print(f"CSV 不存在，先运行 bird ebird export")
        return 1

    # 检查 agent-browser
    if subprocess.run(["which", "agent-browser"], capture_output=True).returncode:
        print("agent-browser 未安装，先运行：")
        print("  npm install -g agent-browser && agent-browser install")
        return 1

    print("打开 eBird 上传页面…")
    r = _run(["agent-browser", "open", "https://ebird.org/import/upload.html"])
    if "login" in r.lower():
        print("\n需要登录 eBird。")
        print("请在弹出的浏览器窗口里登录，登录完成后按回车继续…")
        input()
        r = _run(["agent-browser", "open",
                  "https://ebird.org/import/upload.html"])

    print("等待页面加载…")
    time.sleep(3)
    _run(["agent-browser", "wait", "--load", "networkidle"])

    # 找文件上传输入框
    snap = _run(["agent-browser", "snapshot", "-i"])
    upload_ref = None
    for line in snap.splitlines():
        if "file" in line.lower() or "upload" in line.lower() or "csv" in line.lower():
            # 提取 [ref=eXX]
            import re
            m = re.search(r"\[ref=(e\d+)\]", line)
            if m:
                upload_ref = m.group(1)
                break

    if not upload_ref:
        print("没找到文件上传控件，页面结构：")
        print(snap[:1000])
        _run(["agent-browser", "close"])
        return 1

    print(f"上传 {CSV.name}…")
    r = _run(["agent-browser", "type", f"[ref={upload_ref}]", str(CSV)])
    time.sleep(2)

    # 找提交按钮
    snap = _run(["agent-browser", "snapshot", "-i"])
    submit_ref = None
    for line in snap.splitlines():
        if any(k in line.lower() for k in ("submit", "upload", "import", "继续")):
            import re
            m = re.search(r"\[ref=(e\d+)\]", line)
            if m:
                submit_ref = m.group(1)
                break

    if submit_ref:
        print("提交…")
        _run(["agent-browser", "click", f"[ref={submit_ref}]"])
        time.sleep(3)
        print("完成。请检查 https://ebird.org/ 确认导入成功。")
    else:
        print("没找到提交按钮，请手动点击")

    _run(["agent-browser", "close"])
    return 0


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return r.stdout + r.stderr
