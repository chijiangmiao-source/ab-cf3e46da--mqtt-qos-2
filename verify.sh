#!/usr/bin/env bash
# 无 Docker 环境下的本地 verify：pytest -> 页面构建 -> 起服务 HTTP 冒烟。
# 退出码非 0 即失败。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
VENV="${VENV:-.venv-local}"

echo "== [1/3] 后端代码测试 =="
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q -r backend/requirements.txt
fi
( cd backend && "../$VENV/bin/python" -m pytest tests/ -q )

echo "== [2/3] 联调页面构建检查 =="
( cd frontend && npm install --no-audit --no-fund && npm run build )

echo "== [3/3] 启动服务并执行 HTTP 冒烟 =="
DATA="$(mktemp -d)"
AUDIT_STORE_PATH="$DATA/audits.json" STATIC_DIR="$PWD/frontend/dist" \
  "$VENV/bin/uvicorn" --app-dir backend app.main:app --host 127.0.0.1 --port "$PORT" >/tmp/audit-uvicorn.log 2>&1 &
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; rm -rf "$DATA"; }
trap cleanup EXIT

for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break
  sleep 0.5
done
BASE_URL="http://127.0.0.1:$PORT" python3 scripts/smoke.py

echo ""
echo "本地 verify 全部通过。"
