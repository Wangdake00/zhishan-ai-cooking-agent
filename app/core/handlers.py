"""
智膳 AI FastAPI 全局异常处理器

将自定义异常转为统一 JSON 响应，未知异常降级为 500。
"""
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    ZhiShanException,
    BadRequestException,
    AuthException,
)

logger = logging.getLogger(__name__)


def register_exception_handlers(app):
    """注册全局异常处理器到 FastAPI 应用"""

    @app.exception_handler(ZhiShanException)
    async def handle_zhi_shan_exception(request: Request, exc: ZhiShanException):
        """业务异常 -> 统一 JSON 响应"""
        status_code = exc.code
        if 1000 <= status_code < 2000:
            status_code = 400
        elif 2000 <= status_code < 3000:
            status_code = 401 if status_code == 2401 else 403
        elif 3000 <= status_code < 4000:
            status_code = 400
        elif 4000 <= status_code < 5000:
            status_code = 500
        elif status_code >= 5000:
            status_code = 503

        logger.warning(
            f"[{type(exc).__name__}] code={exc.code}, message={exc.message}, "
            f"path={request.url.path}, method={request.method}"
        )
        return JSONResponse(status_code=status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(request: Request, exc: RequestValidationError):
        """请求参数校验异常 -> 400"""
        logger.warning(f"[ValidationError] path={request.url.path}, errors={exc.errors()}")
        return JSONResponse(
            status_code=400,
            content={
                "code": 1400,
                "message": "请求参数校验失败",
                "detail": exc.errors(),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException):
        """原生 HTTP 异常（404 等）-> 统一格式"""
        if exc.status_code == 401:
            return JSONResponse(status_code=401, content={
                "code": 2401, "message": "未授权", "detail": None,
            })
        elif exc.status_code == 404:
            return JSONResponse(status_code=404, content={
                "code": 1404, "message": "资源不存在", "detail": None,
            })
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail), "detail": None},
        )

    @app.exception_handler(Exception)
    async def handle_unknown_exception(request: Request, exc: Exception):
        """未知异常 -> 500，不泄露内部细节"""
        logger.exception(
            f"[UnhandledException] type={type(exc).__name__}, "
            f"path={request.url.path}, method={request.method}, error={exc}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "code": 5000,
                "message": "服务器内部错误",
                "detail": None,
            },
        )