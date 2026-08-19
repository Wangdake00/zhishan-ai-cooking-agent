"""
用户认证路由：注册、登录、获取当前用户
"""
from fastapi import APIRouter, Depends

from app.core.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
)
from app.core.exceptions import (
    AuthException,
    UsernameExistsException,
)
from app.db import user_store
from app.models.schemas import RegisterRequest, LoginRequest, TokenResponse, UserResponse

router = APIRouter()


@router.post("/auth/register", response_model=TokenResponse)
async def register(request: RegisterRequest):
    """注册新用户"""
    if user_store.get_user_by_username(request.username):
        raise UsernameExistsException()

    password_hash = hash_password(request.password)
    user = user_store.create_user(request.username, password_hash)

    token = create_access_token(user["id"], user["username"])
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user["id"],
            username=user["username"],
            created_at=user["created_at"],
        ),
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """用户登录"""
    user = user_store.get_user_by_username(request.username)
    if not user or not verify_password(request.password, user["password_hash"]):
        raise AuthException(message="用户名或密码错误")

    token = create_access_token(user["id"], user["username"])
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=user["id"],
            username=user["username"],
            created_at=user["created_at"],
        ),
    )


@router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return UserResponse(
        id=current_user["id"],
        username=current_user["username"],
        created_at=current_user["created_at"],
    )