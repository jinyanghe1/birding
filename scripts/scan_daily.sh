#!/bin/zsh
# 每晚流水线（launchd 21:00 / 手动均可）：
#   1. 扫描 Photos 增量
#   2. 识别队列非空时跑 auto
#   3. 本地 DB 快照备份（保留 7 份）
#   4. rsync 单向同步到服务器（DB 快照 + 缩略图增量）
#   5. 服务器：停服 → 备份轮换 → 原子换库 → 起服
# 设计原则：scan 是核心资产，sync 失败只记日志不失败。
set -uo pipefail   # 不用 -e：auto/sync 失败不能炸掉扫描链路

ROOT=/Users/hejinyang/WorkBuddy/观鸟skill
PY=/Users/hejinyang/.workbuddy/binaries/python/envs/birdskill/bin/python
LOG=$ROOT/logs/scan_daily.log
KEY=$HOME/.ssh/birding_deploy
SRV=ubuntu@124.223.171.149
REMOTE_DATA='$HOME/birding/data'
ts() { date '+%F %T' }

mkdir -p "$ROOT/logs"
cd "$ROOT"
echo "[$(ts)] === nightly pipeline start ===" >> "$LOG"

# 1. 扫描（失败即中止）
"$PY" -m birdscan.cli scan >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
  echo "[$(ts)] !!! SCAN FAILED，流水线中止" >> "$LOG"
  exit 1
fi

# 2. 条件 auto：队列非空才跑（避免每晚空加载 YOLO/分类模型）
PENDING=$("$PY" -c "from birdscan import store; print(len(store.get_queue('pending', 1)))" 2>/dev/null || echo 0)
if [ "$PENDING" -gt 0 ]; then
  echo "[$(ts)] id_queue pending=$PENDING，跑 auto..." >> "$LOG"
  "$PY" -m birdscan.cli auto >> "$LOG" 2>&1 \
    || echo "[$(ts)] !! auto failed（非致命）" >> "$LOG"
else
  echo "[$(ts)] id_queue 空，跳过 auto" >> "$LOG"
fi

# 3. 本地安全快照（sqlite backup API，即使本地 web 正在写也一致）
SNAP=$ROOT/data/birds.db.snapshot
"$PY" -c "import sqlite3; s=sqlite3.connect('$ROOT/data/birds.db'); d=sqlite3.connect('$SNAP'); s.backup(d); d.close(); s.close()" \
  >> "$LOG" 2>&1 || { echo "[$(ts)] !! 本地快照失败（非致命），跳过同步" >> "$LOG"; exit 0; }
cp "$SNAP" "$ROOT/data/birds.db.bak.nightly.$(date +%Y%m%d)"
ls -t "$ROOT"/data/birds.db.bak.nightly.* 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null

# 4. rsync：DB 快照 + 缩略图增量（不加 --delete，孤儿缩略图无害）
SSH_OPTS="-i $KEY -o BatchMode=yes -o ConnectTimeout=15"
rsync -az --partial --timeout=300 -e "ssh $SSH_OPTS" \
  "$SNAP" "$SRV:$REMOTE_DATA/birds.db.new" >> "$LOG" 2>&1 \
  || { echo "[$(ts)] !! DB rsync failed（非致命）" >> "$LOG"; exit 0; }
rsync -az --partial --timeout=300 -e "ssh $SSH_OPTS" \
  "$ROOT/data/thumbs/" "$SRV:$REMOTE_DATA/thumbs/" >> "$LOG" 2>&1 \
  || echo "[$(ts)] !! thumbs rsync failed（非致命）" >> "$LOG"

# 5. 服务器：停服（WAL 落盘）→ 清 WAL 残留 → 备份轮换 → 原子换库 → 起服
ssh $SSH_OPTS "$SRV" '
  set -e
  sudo systemctl stop birding
  cd ~/birding/data
  rm -f birds.db-wal birds.db-shm
  [ -f birds.db ] && cp birds.db birds.db.bak.$(date +%Y%m%d)
  ls -t birds.db.bak.???????? 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null || true
  mv -f birds.db.new birds.db
  sudo systemctl start birding
' >> "$LOG" 2>&1 \
  || { echo "[$(ts)] !! 远端换库失败，请人工检查服务器（birds.db.new 未换入）" >> "$LOG"; exit 0; }

echo "[$(ts)] === nightly pipeline done ===" >> "$LOG"
