# ---- 阶段 1：构建联调页面 ----
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 阶段 2：后端运行时 ----
FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATIC_DIR=/app/static \
    AUDIT_STORE_PATH=/data/audits.json

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/app /app/app
COPY scripts /app/scripts
COPY --from=frontend /frontend/dist /app/static

RUN mkdir -p /data && chown -R nobody:nogroup /data /app
USER nobody

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3).read()" || exit 1

# /data 卷保留裁决证据；宿主机端口通过 compose.yaml 的 HOST_PORT 可配置。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- 阶段 3：verify（代码测试 + 页面构建检查 + HTTP 冒烟） ----
FROM node:20-bookworm-slim AS verify
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./

COPY backend /app/backend
COPY scripts /app/scripts
COPY verify-entrypoint.sh /app/verify-entrypoint.sh
RUN chmod +x /app/verify-entrypoint.sh

CMD ["/app/verify-entrypoint.sh"]
