"""
用户长期偏好存储（PostgreSQL / SQLAlchemy 2.0 async）

承载跨会话记忆：忌口、过敏、饮食目标等稳定约束。
由 stream_chat 在检测到显式触发词时抽取并 upsert，图节点每轮读取拼进 system prompt。
"""
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.common.logger import logger
from app.db.database import AsyncSessionLocal
from app.db.models import UserPreference


async def upsert_preference(user_id: int, category: str, content: str, source: str = "explicit") -> None:
    """写入一条偏好（同用户同类别同内容幂等，冲突时只刷新 updated_at）"""
    now = func.now()
    stmt = (
        pg_insert(UserPreference)
        .values(
            user_id=user_id,
            category=(category or "其他")[:32],
            content=(content or "")[:200],
            source=source[:16],
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_user_preferences_user_category_content",
            set_={"updated_at": now},
        )
    )
    async with AsyncSessionLocal() as session:
        await session.execute(stmt)
        await session.commit()
    logger.info(f"[Preference] 偏好写入成功: user_id={user_id}, category={category}")


async def get_preferences(user_id: int, limit: int = 20) -> list[dict]:
    """按更新时间倒序返回该用户最近的偏好（用于注入 system prompt）"""
    async with AsyncSessionLocal() as session:
        rows = await session.scalars(
            select(UserPreference)
            .where(UserPreference.user_id == user_id)
            .order_by(UserPreference.updated_at.desc())
            .limit(max(limit, 1))
        )
        return [{"category": row.category, "content": row.content} for row in rows]


async def list_preferences(user_id: int, limit: int = 100) -> list[dict]:
    """返回该用户偏好明细（带 id 与时间，供前端管理面板展示）"""
    async with AsyncSessionLocal() as session:
        rows = await session.scalars(
            select(UserPreference)
            .where(UserPreference.user_id == user_id)
            .order_by(UserPreference.updated_at.desc())
            .limit(max(limit, 1))
        )
        return [
            {
                "id": row.id,
                "category": row.category,
                "content": row.content,
                "source": row.source,
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            }
            for row in rows
        ]


async def delete_preference(user_id: int, preference_id: int) -> bool:
    """删除一条偏好（按 user_id 限定归属），返回是否删除成功"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(UserPreference).where(
                UserPreference.id == preference_id,
                UserPreference.user_id == user_id,
            )
        )
        await session.commit()
        deleted = (result.rowcount or 0) > 0
    if deleted:
        logger.info(f"[Preference] 偏好删除成功: user_id={user_id}, id={preference_id}")
    return deleted
