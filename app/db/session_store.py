"""
会话元数据存储（PostgreSQL / SQLAlchemy 2.0 async）

独立于 LangGraph 的 checkpoint 表，单独维护一张 sessions 表，
用于列出所有会话、记录标题与更新时间，避免依赖 checkpointer 的内部表结构。
"""
from typing import Optional

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.common.logger import logger
from app.db.database import AsyncSessionLocal
from app.db.models import SessionModel


def _to_dict(row: SessionModel) -> dict:
    """ORM 对象转 dict，时间序列化为 ISO 字符串（保持 API 契约不变）"""
    return {
        "thread_id": row.thread_id,
        "user_id": row.user_id,
        "title": row.title,
        "created_at": row.created_at.isoformat(timespec="seconds"),
        "updated_at": row.updated_at.isoformat(timespec="seconds"),
    }


async def upsert_session(thread_id: str, user_id: int, title: Optional[str] = None) -> None:
    """
    新建或更新会话（PostgreSQL ON CONFLICT 原子 upsert，无并发竞态）：
    - 不存在时插入（title 用传入值或默认"新会话"）
    - 已存在时只刷新 updated_at（不覆盖已有标题，避免被后续消息覆盖掉首条标题）
    """
    now = func.now()
    stmt = (
        pg_insert(SessionModel)
        .values(
            thread_id=thread_id,
            user_id=user_id,
            title=title or "新会话",
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[SessionModel.thread_id],
            set_={"updated_at": now},
        )
    )
    async with AsyncSessionLocal() as session:
        await session.execute(stmt)
        await session.commit()
        logger.info(f"upsert_session: thread_id={thread_id}, user_id={user_id}, title={title}")


async def list_sessions(user_id: int) -> list[dict]:
    """按更新时间倒序返回指定用户的所有会话"""
    async with AsyncSessionLocal() as session:
        rows = await session.scalars(
            select(SessionModel)
            .where(SessionModel.user_id == user_id)
            .order_by(SessionModel.updated_at.desc())
        )
        return [_to_dict(r) for r in rows]


async def delete_session(thread_id: str, user_id: int) -> None:
    """删除会话元数据记录（只能删自己的）"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            sa_delete(SessionModel).where(
                SessionModel.thread_id == thread_id, SessionModel.user_id == user_id
            )
        )
        await session.commit()
        logger.info(f"delete_session: thread_id={thread_id}, user_id={user_id}")


async def get_session(thread_id: str) -> Optional[dict]:
    """根据 thread_id 查询会话元数据（用于校验归属），不存在返回 None"""
    async with AsyncSessionLocal() as session:
        row = await session.get(SessionModel, thread_id)
        return _to_dict(row) if row else None
