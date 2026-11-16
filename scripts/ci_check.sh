#!/usr/bin/env bash
# 本地 CI：与 GitHub Actions 一样，任何测试失败都返回非零。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
SERVER_PID=""
E2E_DATA=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "=== 1. 语法检查 ==="
"$PYTHON" -m py_compile birdscan/*.py tests/e2e_fixture.py

echo "=== 2. 全部单测 ==="
"$PYTHON" -m pytest tests/ -v

echo "=== 3. 独立 fixture 上的 Playwright E2E ==="
"$PYTHON" -c 'import playwright' >/dev/null 2>&1 || {
  echo "缺少 Playwright：请安装依赖并执行 python -m playwright install chromium" >&2
  exit 1
}
E2E_DATA="$(mktemp -d "${TMPDIR:-/tmp}/birding-e2e.XXXXXX")"
E2E_PORT="$("$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
SERVER_LOG="$E2E_DATA/server.log"
BIRDING_API_KEY=dev-key "$PYTHON" tests/e2e_fixture.py \
  --data-dir "$E2E_DATA" --port "$E2E_PORT" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

"$PYTHON" - "$E2E_PORT" "$SERVER_PID" <<'PY' || {
import os
import sys
import time
import urllib.request

port, pid = int(sys.argv[1]), int(sys.argv[2])
url = f"http://127.0.0.1:{port}/api/stats"
for _ in range(30):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status == 200:
                print("测试服务健康检查通过")
                break
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            raise SystemExit("测试服务提前退出")
        time.sleep(1)
else:
    raise SystemExit("测试服务 30 秒内未就绪")
PY
  cat "$SERVER_LOG" >&2
  exit 1
}

BIRDING_BASE="http://127.0.0.1:$E2E_PORT" BIRDING_TEST_KEY=dev-key \
  "$PYTHON" tests/e2e_test.py

echo "=== 4. 生产服务器健康检查 ==="
curl --fail --silent --show-error --max-time 20 \
  http://124.223.171.149/api/stats >/dev/null

echo "=== 全部检查通过 ==="
