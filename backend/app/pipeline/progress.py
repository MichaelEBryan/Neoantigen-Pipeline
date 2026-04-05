"""
Redis pub/sub helper for broadcasting pipeline progress to WebSocket clients.

Pattern:
  - The orchestrator calls publish_progress() after each _log_step().
  - The WebSocket handler subscribes to the analysis channel and forwards
    messages as JSON to the connected client.

Channel naming: "analysis:{analysis_id}:progress"

Messages are JSON with: step, status, message, timestamp, progress_pct
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

# Lazy-initialized async Redis client for pub/sub.
# Separate from Celery's Redis connection (different use case).
_redis_client: Optional[aioredis.Redis] = None


def _channel_name(analysis_id: int) -> str:
    return f"analysis:{analysis_id}:progress"


async def get_redis() -> aioredis.Redis:
    """Get or create the async Redis client for pub/sub."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _redis_client


async def publish_progress(
    analysis_id: int,
    step: str,
    status: str,
    message: str,
    progress_pct: float,
) -> None:
    """
    Publish a pipeline progress event to the Redis channel.
    Called from the orchestrator alongside _log_step.

    progress_pct: 0.0 to 1.0
    """
    try:
        r = await get_redis()
        payload = json.dumps({
            "analysis_id": analysis_id,
            "step": step,
            "status": status,
            "message": message,
            "progress_pct": round(progress_pct, 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await r.publish(_channel_name(analysis_id), payload)
    except Exception as e:
        # Don't let pub/sub failures crash the pipeline
        logger.warning(f"Failed to publish progress for analysis {analysis_id}: {e}")


async def publish_terminal(
    analysis_id: int,
    final_status: str,
    message: str,
) -> None:
    """
    Publish a terminal event (complete/failed/cancelled).
    The WS handler uses this to know when to close the connection.
    """
    try:
        r = await get_redis()
        payload = json.dumps({
            "analysis_id": analysis_id,
            "step": "done" if final_status == "complete" else "error",
            "status": final_status,
            "message": message,
            "progress_pct": 1.0 if final_status == "complete" else -1.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "terminal": True,
        })
        await r.publish(_channel_name(analysis_id), payload)
    except Exception as e:
        logger.warning(f"Failed to publish terminal event for analysis {analysis_id}: {e}")


async def subscribe_progress(analysis_id: int):
    """
    Returns an async Redis pubsub object subscribed to the analysis channel.
    Caller is responsible for unsubscribing and closing.
    """
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_channel_name(analysis_id))
    return pubsub


async def store_celery_task_id(analysis_id: int, task_id: str) -> None:
    """Store the Celery task ID in Redis for cancellation support."""
    r = await get_redis()
    await r.set(f"analysis:{analysis_id}:celery_task_id", task_id, ex=86400 * 7)


async def get_celery_task_id(analysis_id: int) -> Optional[str]:
    """Retrieve the Celery task ID for an analysis."""
    r = await get_redis()
    return await r.get(f"analysis:{analysis_id}:celery_task_id")


async def cleanup_redis() -> None:
    """Close the Redis connection on shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
