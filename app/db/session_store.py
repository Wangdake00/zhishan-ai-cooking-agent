"""
会话元数据存储

独立于 LangGraph 的 SqliteSaver，单独维护一张 sessions 表，
用于列出所有会话、记录标题与更新时间，避免依赖 checkpointer 的内部表结构。
"""
import os
import sqlite3
from datetime import datetime
from typing import Optional

from app.common.logger import logger
from app.core.config import settings

DB_PATH = str(settings.db_path)


def _get_conn() -> sqlite3.Connection:
    """复用同一个数据库文件，建表（如果不存在）"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            thread_id   TEXT PRIMARY KEY,
            user_id     INTEGER,
            title       TEXT NOT NULL DEFAULT '新会话',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """
    )
    # 迁移：如果旧表没有 user_id 列，则添加（SQLite 支持 ALTER TABLE ADD COLUMN）
    try:
        conn.execute("SELECT user_id FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER")
        logger.info("迁移：sessions 表新增 user_id 列")
    conn.commit()
    return conn


def upsert_session(thread_id: str, user_id: int, title: Optional[str] = None) -> None:
    """
    新建或更新会话：
    - 不存在时插入（title 用传入值或默认“新会话”）
    - 已存在时只刷新 updated_at（不覆盖已有标题，避免被后续消息覆盖掉首条标题）
    """
    now = datetime.now().isoformat(timespec="seconds")
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT thread_id FROM sessions WHERE thread_id = ?", (thread_id,)
        ).fetchone()

        if existing is None:
            conn.execute(
                "INSERT INTO sessions (thread_id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (thread_id, user_id, title or "新会话", now, now),
            )
        else:
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE thread_id = ?",
                (now, thread_id),
            )
        conn.commit()
        logger.info(f"upsert_session: thread_id={thread_id}, user_id={user_id}, title={title}")
    finally:
        conn.close()


def list_sessions(user_id: int) -> list[dict]:
    """按更新时间倒序返回指定用户的所有会话"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT thread_id, title, created_at, updated_at FROM sessions WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_session(thread_id: str, user_id: int) -> None:
    """删除会话元数据记录（只能删自己的）"""
    conn = _get_conn()
    try:
        conn.execute(
            "DELETE FROM sessions WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )
        conn.commit()
        logger.info(f"delete_session: thread_id={thread_id}, user_id={user_id}")
    finally:
        conn.close()


def get_session(thread_id: str) -> Optional[dict]:
    """根据 thread_id 查询会话元数据（用于校验归属），不存在返回 None"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT thread_id, user_id, title, created_at, updated_at FROM sessions WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def assign_orphan_sessions_to_user(user_id: int) -> int:
    """
    将所有 user_id 指向无效用户的会话（孤儿会话）关联到指定用户。
    返回被更新的会话数量。
    """
    conn = _get_conn()
    try:
        # 找出所有 user_id 指向不存在用户的会话
        orphan = conn.execute("""
            SELECT s.thread_id FROM sessions s
            WHERE s.user_id IS NOT NULL
            AND s.user_id NOT IN (SELECT id FROM users)
        """).fetchall()
        
        if orphan:
            thread_ids = [row["thread_id"] for row in orphan]
            placeholders = ",".join(["?"] * len(thread_ids))
            conn.execute(
                f"UPDATE sessions SET user_id = ? WHERE thread_id IN ({placeholders})",
                [user_id] + thread_ids
            )
            conn.commit()
            logger.info(f"assign_orphan_sessions_to_user: {len(thread_ids)} sessions assigned to user_id={user_id}")
            return len(thread_ids)
        return 0
    finally:
        conn.close()
