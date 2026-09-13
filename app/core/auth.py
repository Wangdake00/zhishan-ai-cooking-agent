"""
认证模块：密码哈希、JWT 生成与验证、FastAPI 依赖注入
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.exceptions import AuthException, TokenExpiredException
from app.db import user_store

# Bearer token 提取器
security = HTTPBearer()


def hash_password(password: str) -> str:
    """密码哈希（bcrypt，截断到 72 字节以兼容限制）"""
    pwd_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码"""
    pwd_bytes = plain.encode("utf-8")[:72]
    return bcrypt.checkpw(pwd_bytes, hashed.encode("utf-8"))


def create_access_token(user_id: int, username: str) -> str:
    """生成 JWT token"""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.jwt_expire_days),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    """解码 JWT，返回 payload 或 None"""
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise TokenExpiredException()
    except jwt.PyJWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    FastAPI 依赖：从 Authorization header 提取 token，验证并返回当前用户。
    """
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise AuthException(message="Token 无效")

    user_id = payload.get("user_id")
    user = await user_store.get_user_by_id(user_id)
    if user is None:
        raise AuthException(message="用户不存在")
    return user