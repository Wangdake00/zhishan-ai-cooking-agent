"""
智膳 AI 自定义异常体系

统一错误码 + 分层异常类，便于全局处理和前端分类响应。
错误码规则：
    1xxx - 通用异常
    2xxx - 认证异常
    3xxx - 业务异常
    4xxx - RAG / Agent 异常
    5xxx - 第三方服务异常
"""
from typing import Any, Optional


class ZhiShanException(Exception):
    """智膳 AI 基础异常，所有业务异常的父类"""

    def __init__(self, code: int = 1000, message: str = "未知错误", detail: Optional[Any] = None):
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


# ========== 通用异常 (1xxx) ==========

class BadRequestException(ZhiShanException):
    def __init__(self, message: str = "请求参数错误", detail: Optional[Any] = None):
        super().__init__(code=1400, message=message, detail=detail)


class NotFoundException(ZhiShanException):
    def __init__(self, resource: str = "资源", detail: Optional[Any] = None):
        super().__init__(code=1404, message=f"{resource}不存在", detail=detail)


# ========== 认证异常 (2xxx) ==========

class AuthException(ZhiShanException):
    def __init__(self, message: str = "认证失败", detail: Optional[Any] = None):
        super().__init__(code=2401, message=message, detail=detail)


class TokenExpiredException(AuthException):
    def __init__(self):
        super().__init__(message="Token 已过期，请重新登录")


class PermissionDeniedException(AuthException):
    def __init__(self, message: str = "无权操作该资源"):
        super().__init__(code=2403, message=message)


# ========== 业务异常 (3xxx) ==========

class BusinessException(ZhiShanException):
    def __init__(self, message: str = "业务处理失败", detail: Optional[Any] = None):
        super().__init__(code=3000, message=message, detail=detail)


class UsernameExistsException(BusinessException):
    def __init__(self):
        super().__init__(code=3409, message="用户名已被占用")


# ========== RAG / Agent 异常 (4xxx) ==========

class RAGException(ZhiShanException):
    def __init__(self, message: str = "RAG 检索失败", detail: Optional[Any] = None):
        super().__init__(code=4000, message=message, detail=detail)


class RAGNotFoundException(RAGException):
    def __init__(self, message: str = "未找到相关菜谱", detail: Optional[Any] = None):
        super().__init__(code=4404, message=message, detail=detail)


class AgentToolException(ZhiShanException):
    def __init__(self, message: str = "Agent 工具调用失败", tool: str = "", detail: Optional[Any] = None):
        super().__init__(code=4001, message=f"{tool} 工具调用失败: {message}" if tool else message, detail=detail)
        self.tool = tool


class IntentDetectionException(ZhiShanException):
    def __init__(self, message: str = "意图检测失败", detail: Optional[Any] = None):
        super().__init__(code=4002, message=message, detail=detail)


# ========== 第三方服务异常 (5xxx) ==========

class ExternalServiceException(ZhiShanException):
    def __init__(self, service: str, message: str = "外部服务调用失败", detail: Optional[Any] = None):
        super().__init__(code=5000, message=f"[{service}] {message}", detail=detail)
        self.service = service


class LLMTimeoutException(ExternalServiceException):
    def __init__(self, model: str = "", detail: Optional[Any] = None):
        super().__init__(service="LLM", message=f"模型 {model} 响应超时" if model else "模型响应超时", detail=detail)


class RerankServiceException(ExternalServiceException):
    def __init__(self, message: str = "Rerank 服务异常", detail: Optional[Any] = None):
        super().__init__(service="Rerank", message=message, detail=detail)


class OSSServiceException(ExternalServiceException):
    def __init__(self, message: str = "OSS 服务异常", detail: Optional[Any] = None):
        super().__init__(service="OSS", message=message, detail=detail)