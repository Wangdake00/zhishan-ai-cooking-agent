# ============================================================
# 智膳 AI 后端镜像（多阶段构建）
#   阶段一 frontend-builder：node:22-alpine 构建前端静态产物
#   阶段二 runtime：python:3.13-slim + uv 安装后端依赖，托管前端产物
# 密钥安全：.env 由 .dockerignore 排除且从不 COPY，密钥仅通过
#           docker-compose 的 env_file 在运行时注入容器
# ============================================================

# ---------- 阶段一：前端构建（Next.js 静态导出 → out/） ----------
FROM node:22-alpine AS frontend-builder

ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /build

# 先只复制依赖清单并安装，充分利用 Docker 层缓存（源码变动不重装依赖）
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# 再复制前端源码并构建静态导出（next.config.ts 已配置 output: "export"）
COPY frontend/ ./
RUN npm run build    # 产出 /build/out/


# ---------- 阶段二：后端运行时（FastAPI + uvicorn） ----------
FROM python:3.13-slim AS runtime

# 从官方镜像直接复制 uv 二进制（不引入 pip/curl 额外组件）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# 依赖层缓存优化：先只 COPY 依赖清单，uv sync --frozen 严格按 uv.lock 锁定版本安装；
# 源码变动时此层直接命中缓存，跳过依赖安装
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 复制后端源码与 Alembic 迁移（app/static 由前端构建产物生成，禁止从宿主机 COPY）
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# 前端静态产物 → FastAPI 托管目录（catch-all 路由从此目录服务）
COPY --from=frontend-builder /build/out/ ./app/static/

# 启动脚本：sed 兜底清除 Windows 可能引入的 CRLF，防止 Linux 容器内解析失败
COPY entrypoint.sh ./
RUN sed -i 's/\r$//' entrypoint.sh \
    # 非 root 用户运行；预创建数据目录并授权，
    # 命名卷首次挂载时会继承该目录属主，保证容器内进程可写
    && useradd --create-home --shell /bin/sh appuser \
    && mkdir -p /app/db/chroma_data \
    && chown -R appuser:appuser /app \
    && chmod +x entrypoint.sh

USER appuser

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8001

ENTRYPOINT ["./entrypoint.sh"]
