#!/bin/bash
# 一键部署到腾讯云服务器
# 用法: ./scripts/deploy_to_server.sh [--data]

set -e

KEY="$HOME/Downloads/birding.pem"
SERVER="ubuntu@124.223.171.149"
LOCAL_DIR="/Users/hejinyang/WorkBuddy/观鸟skill"

echo "=== 打包代码 ==="
cd "$LOCAL_DIR"
tar czf /tmp/birding.tar.gz --exclude='__pycache__' --exclude='*.pyc' birdscan/

echo "=== 上传代码 ==="
scp -i "$KEY" /tmp/birding.tar.gz "$SERVER:~/"

echo "=== 解压并重启 ==="
ssh -i "$KEY" "$SERVER" << 'REMOTE'
cd ~/birding
tar xzf ~/birding.tar.gz
# 静态文件也要部署（PWA manifest/sw.js）
cp -r birdscan/static/* /var/www/html/ 2>/dev/null || true
sudo systemctl restart birding
sleep 2
sudo systemctl status birding --no-pager | head -5
REMOTE

if [ "$1" == "--data" ]; then
  echo "=== 打包数据 ==="
  cd "$LOCAL_DIR/data"
  tar czf /tmp/birding_data.tar.gz birds.db thumbs/

  echo "=== 上传数据（约 37MB，需要几分钟） ==="
  scp -i "$KEY" /tmp/birding_data.tar.gz "$SERVER:~/"

  echo "=== 解压数据 ==="
  ssh -i "$KEY" "$SERVER" << 'REMOTE'
cd ~/birding
tar xzf ~/birding_data.tar.gz
sudo systemctl restart birding
REMOTE
fi

echo ""
echo "=== 完成 ==="
echo "访问: http://124.223.171.149"
