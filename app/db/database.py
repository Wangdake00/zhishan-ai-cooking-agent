"""
SQLAlchemy 异步数据库基础设施

- engine: 全局异步连接池（asyncpg 驱动）
- AsyncSessionLocal: 会话工厂
- get_db: FastAPI 依赖注入用的异步生成器
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,  # 取连接前探活，避免 PG 重启后拿到失效连接
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类，metadata 供 Alembic 使用"""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：请求级会话，用完自动归还连接池"""
    async with AsyncSessionLocal() as session:
        yield session
