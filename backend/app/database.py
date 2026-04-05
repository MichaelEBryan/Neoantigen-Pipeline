import asyncio
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

logger = logging.getLogger(__name__)


def _create_engine_with_retry(
    database_url: str,
    max_retries: int = 3,
    retry_delay: int = 2,
) -> any:
    """
    Create async engine with retry logic for initial connection failures.

    Attempts to create engine multiple times to handle startup delays
    in containerized environments where DB may not be ready immediately.

    Args:
        database_url: Database connection URL
        max_retries: Maximum number of connection attempts
        retry_delay: Seconds to wait between retries

    Returns:
        SQLAlchemy async engine
    """
    for attempt in range(max_retries):
        try:
            # Create async engine with connection pooling
            engine = create_async_engine(
                database_url,
                echo=False,
                future=True,
                pool_size=20,
                max_overflow=10,
                pool_timeout=30,
                pool_pre_ping=True,  # Validate connections before use
                pool_recycle=3600,  # Recycle connections after 1 hour
                connect_args={"server_settings": {"jit": "off"}},
            )
            logger.info(f"Database engine created successfully on attempt {attempt + 1}")
            return engine
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"Failed to create database engine (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                # In sync context, we can't use await, so we use time.sleep
                # This is called at module load time before any async context
                import time
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to create database engine after {max_retries} attempts: {e}")
                raise


# Create async engine with connection pooling and retry logic
engine = _create_engine_with_retry(settings.database_url)

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass


async def get_db():
    """Dependency for FastAPI to provide DB session."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
