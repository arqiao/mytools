#!/bin/bash
# OpenClaw SSH 隧道脚本
# 用法: bash openclaw-tunnel.sh

PORT=18789
HOST="openclaw-cloud"

echo "正在建立 OpenClaw SSH 隧道 (本地 $PORT → 服务器 $PORT)..."

# 检查端口是否被占用
if netstat -ano 2>/dev/null | grep -q "127.0.0.1:$PORT.*LISTENING"; then
  PID=$(netstat -ano | grep "127.0.0.1:$PORT.*LISTENING" | head -1 | awk '{print $NF}')
  echo "⚠ 端口 $PORT 已被占用 (PID: $PID)"
  echo "  可能是上次的隧道进程未关闭"
  read -p "  是否杀掉旧进程并重连？(y/N) " choice
  if [[ "$choice" == "y" || "$choice" == "Y" ]]; then
    taskkill //PID "$PID" //F >/dev/null 2>&1
    echo "  已终止旧进程，等待端口释放..."
    sleep 2
  else
    echo "  取消操作"
    exit 1
  fi
fi

# 建立隧道
echo "连接中..."
ssh -N -L ${PORT}:127.0.0.1:${PORT} "$HOST" &
SSH_PID=$!
sleep 2

# 验证隧道
if curl -s -m 3 http://127.0.0.1:${PORT}/tools/invoke \
  -H 'Content-Type: application/json' \
  -d '{"tool": "sessions_list", "args": {}}' | grep -q '"ok":true'; then
  echo "✓ 隧道已建立，OpenClaw Gateway 可访问"
  echo "  地址: http://127.0.0.1:${PORT}"
  echo "  按 Ctrl+C 关闭隧道"
  wait $SSH_PID
else
  echo "✗ 隧道建立失败，请检查服务器状态"
  kill $SSH_PID 2>/dev/null
  exit 1
fi
