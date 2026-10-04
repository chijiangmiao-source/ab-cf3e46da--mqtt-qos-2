#!/bin/sh
# Compose verify 入口：
#   1. 围绕重连闭合规则运行后端代码测试（pytest）
#   2. 联调页面构建检查（vite build）
#   3. 对运行中的 web 服务执行 API/HTTP 冒烟（合法重发仅一次交付、冲突重发被拒）
# 任一步失败即以非零退出码结束，由 `docker compose run --rm verify` 上报。
set -eu

echo "== [1/3] 后端代码测试：MQTT5 QoS2 重连闭合/重放/未闭合裁决 =="
cd /app/backend
python -m pytest tests/ -q

echo "== [2/3] 联调页面构建检查 =="
cd /frontend
npm run build

echo "== [3/3] API/HTTP 冒烟：BASE_URL=${BASE_URL:-http://web:8000} =="
BASE_URL="${BASE_URL:-http://web:8000}" python /app/scripts/smoke.py

echo ""
echo "verify 全部通过：代码测试、页面构建、HTTP 冒烟。"
