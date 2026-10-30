"""
用户长期偏好存储（PostgreSQL / SQLAlchemy 2.0 async）

承载跨会话记忆：忌口、过敏、饮食目标等稳定约束。
由 stream_chat 在检测到显式触发词时抽取并 upsert，图节点每轮读取拼进 system prompt。
"""
from sqlalchemy import func, select
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
