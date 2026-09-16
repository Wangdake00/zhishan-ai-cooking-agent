"""
用户数据存储（PostgreSQL / SQLAlchemy 2.0 async）
"""
from datetime import timezone
from typing import Optional

from sqlalchemy import select

from app.common.logger import logger
from app.db.database import AsyncSessionLocal
from app.db.models import User


def _to_dict(user: User, with_password: bool = False) -> dict:
    """ORM 对象转 dict，时间序列化为带 UTC 时区标记的 ISO 字符串"""
    data = {
        "id": user.id,
        "username": user.username,
        "created_at": user.created_at.replace(tzinfo=timezone.utc).isoformat(timespec="seconds"),
    }
    if with_password:
        data["password_hash"] = user.password_hash
    return data


async def create_user(username: str, password_hash: str) -> dict:
    """新建用户，返回用户字典"""
    async with AsyncSessionLocal() as session:
        user = User(username=username, password_hash=password_hash)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info(f"create_user: username={username}, id={user.id}")
        return _to_dict(user, with_password=True)


async def get_user_by_username(username: str) -> Optional[dict]:
    """按用户名查询用户"""
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.username == username))
        return _to_dict(user, with_password=True) if user else None


async def get_user_by_id(user_id: int) -> Optional[dict]:
    """按 ID 查询用户"""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        return _to_dict(user) if user else None
