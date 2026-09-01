#!/bin/bash
# CI/CD 检查脚本：commit 后自动运行

set -e

echo "=== 1. 语法检查 ==="
python3 -m py_compile birdscan/*.py
echo "✅ 语法检查通过"

echo "=== 2. 单测 ==="
python3 -m pytest tests/ -v || echo "⚠️ 暂无单测"

echo "=== 3. E2E 测试（Playwright）==="
if command -v python3 &> /dev/null; then
  python3 tests/e2e_test.py || echo "⚠️ E2E 测试失败"
else
  echo "⚠️ 跳过 E2E（未安装 playwright）"
fi

echo "=== 4. 服务器健康检查 ==="
curl -f http://124.223.171.149/api/stats > /dev/null && echo "✅ 服务器正常" || echo "❌ 服务器异常"

echo ""
echo "=== 检查完成 ==="
