"""
收藏食谱存储模块

独立维护 favorites 表，用户可以收藏喜欢的食谱，支持查看、删除。
"""
import os
import sqlite3
from datetime import datetime
from typing import Optional

from app.common.logger import logger
from app.core.config import settings

DB_PATH = str(settings.db_path)


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接并自动建表"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            ingredients TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def create_favorite(user_id: int, title: str, content: str, ingredients: str = "") -> int:
    """
    新增一条收藏记录，返回收藏ID
    """
    now = datetime.now().isoformat(timespec="seconds")
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO favorites (user_id, title, content, ingredients, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, title, content, ingredients, now, now),
        )
        conn.commit()
        favorite_id = cursor.lastrowid
        logger.info(f"create_favorite: id={favorite_id}, user_id={user_id}, title={title}")
        return favorite_id
    finally:
        conn.close()


def list_favorites(user_id: int) -> list[dict]:
    """列出指定用户的所有收藏（按创建时间倒序）"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, content, ingredients, created_at, updated_at FROM favorites WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_favorite(favorite_id: int, user_id: int) -> Optional[dict]:
    """获取单条收藏（只能查看自己的）"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, title, content, ingredients, created_at, updated_at FROM favorites WHERE id = ? AND user_id = ?",
            (favorite_id, user_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_favorite(favorite_id: int, user_id: int) -> bool:
    """删除收藏（只能删自己的），返回是否删除成功"""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM favorites WHERE id = ? AND user_id = ?",
            (favorite_id, user_id),
        )
        conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"delete_favorite: id={favorite_id}, user_id={user_id}")
        return deleted
    finally:
        conn.close()
