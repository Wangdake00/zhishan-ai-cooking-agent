import os
import logging
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import chat
from app.api.v1 import oss
from app.api.v1 import auth
from app.api.v1 import favorites
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2.挂载路由
app.include_router(auth.router, prefix="/api/v1", tags=["用户认证"])
app.include_router(chat.router, prefix="/api/v1", tags=["对话"])
app.include_router(oss.router, prefix="/api/v1", tags=["申请上传签名url"])
app.include_router(favorites.router, prefix="/api/v1", tags=["收藏食谱"])

# 3.挂载前端资源
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

# 4.健康检查端点
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

# 5.前端 fallback 路由 - 只处理非 API 请求
@app.get("/{path:path}", include_in_schema=False)
async def serve_frontend(path: str):
    if path.startswith("api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Not Found"}, status_code=404)
    file_path = os.path.join(static_dir, path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": f"{settings.app_name} 已上线~", "status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug or True,
    )