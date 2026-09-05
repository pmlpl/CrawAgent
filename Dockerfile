# Phase 4.3 — Dockerfile
# Multi-stage build: builder (装依赖) → runtime (最小镜像)
#
# Usage:
#   docker build -t crawagent .
#   docker run -p 8006:8006 -v $(pwd)/data:/app/data -v $(pwd)/output:/app/output -v $(pwd)/downloads:/app/downloads crawagent
#
# 或用 docker-compose up（见 docker-compose.yml）

# === Stage 1: Builder ===
FROM python:3.13-slim AS builder

# 装 uv（比 pip 快很多，Phase 4 推荐统一用 uv）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 先拷依赖文件（利用 Docker 层缓存 — 依赖不变时这层不重跑）
COPY pyproject.toml uv.lock ./

# 装依赖（带 [api] extra，后端 FastAPI/uvicorn 必需）
RUN uv sync --frozen --extra api --no-dev

# === Stage 2: Runtime ===
FROM python:3.13-slim AS runtime

# 系统依赖：curl 健康检查；字体等可选（browser extra 需要）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 复制 Python 环境
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

# 拷应用源码
WORKDIR /app
COPY crawagent ./crawagent
COPY scripts ./scripts
COPY pyproject.toml ./

# 前端构建产物
# 如果 web/dist 存在（已 npm run build），COPY 进来 FastAPI 会自动托管；
# 如果不存在（fresh clone），先 mkdir 一个空目录，COPY 不会报错，
# FastAPI 发现 WEB_DIST 不存在会跳过挂载，后端 REST/WS 路由依然可用。
RUN mkdir -p web/dist
COPY web/dist ./web/dist

# ✅ 关键：editable install 让 Python 能 import crawagent 包
# --no-deps 因为依赖已在 builder 阶段装好，这里只装项目本身
RUN uv pip install -e . --no-deps --python /app/.venv/bin/python

# 环境变量
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    CRAWAGENT_PROJECT_ROOT=/app \
    CRAWAGENT_HOST=0.0.0.0 \
    CRAWAGENT_PORT=8006

# 暴露后端端口
EXPOSE 8006

# 健康检查：每 30s 探测 /docs 是否可访问
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8006/docs || exit 1

# 启动
CMD ["crawagent", "start"]
