"""
收藏食谱存储模块（PostgreSQL / SQLAlchemy 2.0 async）

独立维护 favorites 表，用户可以收藏喜欢的食谱，支持查看、删除。
"""
from datetime import timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.common.logger import logger
from app.db.database import AsyncSessionLocal
from app.db.models import Favorite


def _to_dict(row: Favorite) -> dict:
    """ORM 对象转 dict，时间序列化为带 UTC 时区标记的 ISO 字符串"""
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "ingredients": row.ingredients,
        "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat(timespec="seconds"),
        "updated_at": row.updated_at.replace(tzinfo=timezone.utc).isoformat(timespec="seconds"),
    }


async def create_favorite(user_id: int, title: str, content: str, ingredients: str = "") -> int:
    """新增一条收藏记录，返回收藏ID；同用户同标题重复收藏时返回已有记录ID"""
    async with AsyncSessionLocal() as session:
        row = Favorite(user_id=user_id, title=title, content=content, ingredients=ingredients)
        session.add(row)
        try:
            await session.commit()
            logger.info(f"create_favorite: id={row.id}, user_id={user_id}, title={title}")
            return row.id
        except IntegrityError:
            # uq_favorites_user_title 冲突：已收藏过，返回已有记录
            await session.rollback()
            existing = await session.scalar(
                select(Favorite).where(Favorite.user_id == user_id, Favorite.title == title)
            )
            if existing:
                logger.info(f"create_favorite: 重复收藏，返回已有 id={existing.id}, user_id={user_id}, title={title}")
                return existing.id
            raise


async def list_favorites(user_id: int) -> list[dict]:
    """列出指定用户的所有收藏（按创建时间倒序）"""
    async with AsyncSessionLocal() as session:
        rows = await session.scalars(
            select(Favorite)
            .where(Favorite.user_id == user_id)
            .order_by(Favorite.created_at.desc())
        )
        return [_to_dict(r) for r in rows]


async def get_favorite(favorite_id: int, user_id: int) -> Optional[dict]:
    """获取单条收藏（只能查看自己的）"""
    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(Favorite).where(Favorite.id == favorite_id, Favorite.user_id == user_id)
        )
        return _to_dict(row) if row else None


async def delete_favorite(favorite_id: int, user_id: int) -> bool:
    """删除收藏（只能删自己的），返回是否删除成功"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(Favorite).where(Favorite.id == favorite_id, Favorite.user_id == user_id)
        )
        await session.commit()
        deleted = (result.rowcount or 0) > 0
        if deleted:
            logger.info(f"delete_favorite: id={favorite_id}, user_id={user_id}")
        return deleted
