import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as redis
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Health Check Response Models
# ============================================================================

class HealthCheckResponse:
    """Response structure for health checks."""

    def __init__(self):
        self.status: str = "healthy"  # healthy, degraded, unhealthy
        self.timestamp: str = datetime.now(timezone.utc).isoformat()
        self.checks: dict = {}

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "status": self.status,
            "timestamp": self.timestamp,
            "checks": self.checks,
        }


# ============================================================================
# Health Check Helpers
# ============================================================================

async def check_database() -> dict:
    """
    Check database connectivity.

    Returns: {status, latency_ms, error}
    """
    start_time = time.time()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = (time.time() - start_time) * 1000
        return {
            "status": "healthy",
            "latency_ms": round(latency_ms, 2),
        }
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.error(f"Database health check failed: {e}")
        return {
            "status": "unhealthy",
            "latency_ms": round(latency_ms, 2),
            "error": str(e),
        }


async def check_redis() -> dict:
    """
    Check Redis connectivity.

    Returns: {status, latency_ms, error}
    """
    start_time = time.time()
    redis_client: Optional[redis.Redis] = None
    try:
        # Parse Redis URL to create client
        redis_client = await redis.from_url(settings.redis_url)
        await redis_client.ping()
        latency_ms = (time.time() - start_time) * 1000
        return {
            "status": "healthy",
            "latency_ms": round(latency_ms, 2),
        }
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.error(f"Redis health check failed: {e}")
        return {
            "status": "degraded",  # Redis is optional for core functionality
            "latency_ms": round(latency_ms, 2),
            "error": str(e),
        }
    finally:
        if redis_client:
            await redis_client.close()


async def check_celery() -> dict:
    """
    Check Celery connectivity by inspecting active workers.

    Returns: {status, latency_ms, error, workers}
    """
    start_time = time.time()
    try:
        from app.celery_app import celery_app
        from celery.app.control import Inspect

        inspect = Inspect(app=celery_app)
        # This is synchronous but should be quick
        ping_result = inspect.ping()
        latency_ms = (time.time() - start_time) * 1000

        if ping_result:
            worker_count = len(ping_result)
            return {
                "status": "healthy",
                "latency_ms": round(latency_ms, 2),
                "workers": worker_count,
            }
        else:
            # No workers responding, but Celery is configured
            return {
                "status": "degraded",
                "latency_ms": round(latency_ms, 2),
                "workers": 0,
                "error": "No active workers",
            }
    except ImportError:
        # Celery not configured
        return {
            "status": "degraded",
            "latency_ms": (time.time() - start_time) * 1000,
            "error": "Celery not configured",
        }
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.warning(f"Celery health check failed: {e}")
        return {
            "status": "degraded",
            "latency_ms": round(latency_ms, 2),
            "error": str(e),
        }


# ============================================================================
# Health Check Endpoints
# ============================================================================

@router.get("/health", tags=["health"], status_code=status.HTTP_200_OK)
async def health_check() -> dict:
    """
    Shallow health check - quick response indicating API is running.
    """
    return {
        "status": "healthy",
        "service": "Oxford Cancer Vaccine Design Backend",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/deep", tags=["health"], status_code=status.HTTP_200_OK)
async def deep_health_check() -> dict:
    """
    Deep health check - checks database, Redis, and Celery connectivity.

    Returns:
    - status: healthy (all pass), degraded (some fail), unhealthy (critical fails)
    - timestamp: ISO 8601 datetime
    - checks: {db, redis, celery} with latency_ms and error info
    """
    health = HealthCheckResponse()

    # Run all checks concurrently
    db_check, redis_check, celery_check = await asyncio.gather(
        check_database(),
        check_redis(),
        check_celery(),
        return_exceptions=False,
    )

    health.checks["database"] = db_check
    health.checks["redis"] = redis_check
    health.checks["celery"] = celery_check

    # Determine overall status
    # unhealthy if database fails (critical)
    if db_check.get("status") == "unhealthy":
        health.status = "unhealthy"
    # degraded if any non-critical check fails
    elif any(
        check.get("status") == "degraded"
        for check in [redis_check, celery_check]
    ):
        health.status = "degraded"
    else:
        health.status = "healthy"

    # Return appropriate status code
    status_code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if health.status == "unhealthy"
        else status.HTTP_200_OK
    )

    return JSONResponse(
        content=health.to_dict(),
        status_code=status_code,
    )
