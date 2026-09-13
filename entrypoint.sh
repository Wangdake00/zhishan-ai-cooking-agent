#!/bin/sh
# ============================================================
# 智膳 AI 容器启动入口
#   1. 数据库迁移（fail fast：迁移失败 → 脚本非零退出 → 容器启动失败，不吞错误）
#   2. RAG 向量库预热（首次启动自动调用 DashScope 灌入 100 条菜谱；
#      失败不阻塞启动 —— 对话请求时 RAG 仍有全链路降级，日志中会给出告警）
#   3. exec 前台拉起 uvicorn（作为 PID 1，正确接收 SIGTERM 优雅退出）
# ============================================================
set -e

echo "[entrypoint] 1/3 执行数据库迁移: alembic upgrade head"
alembic upgrade head
echo "[entrypoint] 数据库迁移完成"

echo "[entrypoint] 2/3 预热 RAG 向量库（首次启动将灌入 100 条菜谱，请耐心等待）"
if python -c "from app.common.logger import setup_logging; setup_logging(); from app.rag.vector_store import get_collection; get_collection()"; then
    echo "[entrypoint] RAG 向量库就绪"
else
    echo "[entrypoint] WARNING: RAG 向量库预热失败（请检查 DASHSCOPE_API_KEY 与网络），对话请求将走降级链路" >&2
fi

echo "[entrypoint] 3/3 启动 uvicorn (0.0.0.0:8001)"
exec uvicorn app.main:app --host 0.0.0.0 --port 8001
