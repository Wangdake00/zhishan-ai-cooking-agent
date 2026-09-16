import os
import logging
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import chat
from app.api.v1 import oss
from app.api.v1 import auth
from app.api.v1 import favorites
from app.api.v1 import voice
from app.common.logger import setup_logging
from app.core.config import settings
from app.core.handlers import register_exception_handlers

# 初始化日志配置
setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    description=f"{settings.app_name} API - 多模态智能烹饪助手",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

# 注册全局异常处理器
register_exception_handlers(app)

# 1. 配置跨域资源共享 (CORS)
# 注意：allow_credentials=True 时不能使用 allow_origins=["*"]，
# 白名单从配置读取（CORS_ORIGINS，逗号分隔）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2.挂载路由
app.include_router(auth.router, prefix="/api/v1", tags=["用户认证"])
app.include_router(chat.router, prefix="/api/v1", tags=["对话"])
app.include_router(oss.router, prefix="/api/v1", tags=["申请上传签名url"])
app.include_router(favorites.router, prefix="/api/v1", tags=["收藏食谱"])
app.include_router(voice.router, prefix="/api/v1", tags=["语音识别"])

# 3.健康检查端点
@app.get("/health")
async def health_check():
    """健康检查：返回服务状态、版本、依赖状态"""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": "production" if settings.is_production else "development",
        "llm_model": settings.llm_model,
    }


@app.get("/health/ready")
async def health_ready():
    """就绪探针：校验核心依赖（PostgreSQL）是否可用，失败返回 503"""
    from sqlalchemy import text

    from app.db.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "ok"}
    except Exception as e:
        logger.error(f"[health/ready] 数据库不可用: {e}")
        return JSONResponse(
            {"status": "unready", "database": "unavailable"}, status_code=503
        )

# 4.前端 fallback 路由 - 只处理非 API 请求
# 注意：必须注册在所有 API 路由之后；此通配路由接管全部 GET 请求，
# 静态文件由这里服务（原来挂在 "/" 上的 StaticFiles 会遮蔽 /health 与本路由，已移除）
static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))

@app.get("/{path:path}", include_in_schema=False)
async def serve_frontend(path: str):
    """静态文件 → 目录路由 index → SPA fallback → JSON 兜底"""
    if path.startswith("api/"):
        return JSONResponse({"error": "Not Found"}, status_code=404)
    # 防路径遍历：规范化后必须仍位于 static 目录内（拦截 ..、绝对路径等）
    file_path = os.path.normpath(os.path.join(static_dir, path))
    if file_path != static_dir and not file_path.startswith(static_dir + os.sep):
        return JSONResponse({"error": "Not Found"}, status_code=404)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    # 目录型路由（Next.js 静态导出：/login → login/index.html）
    dir_index = os.path.join(file_path, "index.html")
    if os.path.isfile(dir_index):
        return FileResponse(dir_index)
    # SPA fallback：未命中静态资源时回退根 index.html
    index_path = os.path.join(static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"message": f"{settings.app_name} 已上线~", "status": "ok"}


if __name__ == "__main__":
    import asyncio
    import sys

    import uvicorn

    if sys.platform == "win32":
        # psycopg async（AsyncPostgresSaver）在 Windows 上不支持默认的 ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    reload = settings.debug
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=reload,
        # reload 模式子进程由 uvicorn 自动选 SelectorEventLoop；
        # 非 reload 模式需走 policy（上面已设为 Selector）
        loop="none" if not reload else "auto",
    )