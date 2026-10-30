"""add user_preferences table

- 新增用户跨会话长期偏好表（忌口/过敏/目标等）
- 仅新增表，不修改既有表，存量数据零影响
- 用户删除时级联删除其偏好

Revision ID: d9c2e5a7b3f1
Revises: b7f4c9e2a1d3
Create Date: 2026-10-30 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd9c2e5a7b3f1'
down_revision: Union[str, Sequence[str], None] = 'b7f4c9e2a1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'user_preferences',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('content', sa.String(length=200), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_user_preferences_user_id_users',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'category', 'content',
                            name='uq_user_preferences_user_category_content'),
    )
    op.create_index('ix_user_preferences_user_id', 'user_preferences', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_user_preferences_user_id', table_name='user_preferences')
    op.drop_table('user_preferences')
