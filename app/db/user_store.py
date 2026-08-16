"""
用户数据存储
"""
import sqlite3
from datetime import datetime
from typing import Optional

from app.common.logger import logger

DB_PATH = "db/personal_chief.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def create_user(username: str, password_hash: str) -> dict:
    """新建用户，返回用户字典"""
    now = datetime.now().isoformat(timespec="seconds")
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, now),
        )
        conn.commit()
        user = get_user_by_username(username)
        logger.info(f"create_user: username={username}, id={user['id']}")
        return user
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[dict]:
    """按用户名查询用户"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    """按 ID 查询用户"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, username, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
