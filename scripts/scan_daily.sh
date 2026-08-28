#!/bin/zsh
# 每日扫描入口（launchd / 手动均可）
set -euo pipefail
ROOT=/Users/hejinyang/WorkBuddy/观鸟skill
PY=/Users/hejinyang/.workbuddy/binaries/python/envs/birdskill/bin/python
mkdir -p "$ROOT/logs"
exec "$PY" -m birdscan.cli scan >> "$ROOT/logs/scan_daily.log" 2>&1
