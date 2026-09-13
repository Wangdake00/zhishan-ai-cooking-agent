"""add FK/unique constraints and cleanup dirty data

- 清理孤儿会话/孤儿收藏（user_id 指向不存在的用户）
- 清理重复收藏（同用户同标题，保留最早一条）
- sessions.user_id → users.id 外键（用户删除时会话置为孤儿，SET NULL）
- favorites.user_id → users.id 外键（用户删除时级联删除收藏）
- favorites (user_id, title) 唯一约束，防止重复收藏

Revision ID: b7f4c9e2a1d3
Revises: 3e3a1409632f
Create Date: 2026-09-13 23:55:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7f4c9e2a1d3'
down_revision: Union[str, Sequence[str], None] = '3e3a1409632f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. 脏数据清理：孤儿会话（user_id 非空但用户不存在）
    op.execute(
        """
        DELETE FROM sessions s
        WHERE s.user_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = s.user_id)
        """
    )
    # 孤儿收藏
    op.execute(
        """
        DELETE FROM favorites f
        WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = f.user_id)
        """
    )
    # 重复收藏：同一用户同一标题只保留最早一条
    op.execute(
        """
        DELETE FROM favorites f
        USING (
            SELECT user_id, title, min(id) AS keep_id
            FROM favorites
            GROUP BY user_id, title
            HAVING count(*) > 1
        ) d
        WHERE f.user_id = d.user_id
          AND f.title = d.title
          AND f.id > d.keep_id
        """
    )

    # 2. 外键约束
    op.create_foreign_key(
        'fk_sessions_user_id_users', 'sessions', 'users',
        ['user_id'], ['id'], ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_favorites_user_id_users', 'favorites', 'users',
        ['user_id'], ['id'], ondelete='CASCADE',
    )

    # 3. 唯一约束：防止同一用户重复收藏同名食谱
    op.create_unique_constraint(
        'uq_favorites_user_title', 'favorites', ['user_id', 'title']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_favorites_user_title', 'favorites', type_='unique')
    op.drop_constraint('fk_favorites_user_id_users', 'favorites', type_='foreignkey')
    op.drop_constraint('fk_sessions_user_id_users', 'sessions', type_='foreignkey')
