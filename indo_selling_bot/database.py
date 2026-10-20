"""
Database engine and session management using SQLAlchemy 2.0 (Async).
Optimized for local SQLite on Windows with WAL mode and foreign key support.
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event
from indo_selling_bot.config import settings

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass

# Create async engine for SQLite
# SQLite requires connect_args for multithreading / async
connect_args = {"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def init_db() -> None:
    """Initialize database tables and enforce SQLite pragmas (WAL mode, foreign keys)."""
    import indo_selling_bot.models  # Ensure all model tables are registered in Base.metadata
    async with engine.begin() as conn:
        if "sqlite" in settings.DATABASE_URL:
            # Enable SQLite foreign keys and WAL mode for high performance
            await conn.exec_driver_sql("PRAGMA foreign_keys = ON;")
            await conn.exec_driver_sql("PRAGMA journal_mode = WAL;")
            await conn.exec_driver_sql("PRAGMA synchronous = NORMAL;")
            await conn.exec_driver_sql("PRAGMA cache_size = -64000;")  # 64MB memory cache
            await conn.exec_driver_sql("PRAGMA temp_store = MEMORY;")
            await conn.exec_driver_sql("PRAGMA mmap_size = 268435456;") # 256MB memory mapped I/O
        await conn.run_sync(Base.metadata.create_all)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database sessions in FastAPI routes and services."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
