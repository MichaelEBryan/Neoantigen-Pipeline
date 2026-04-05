"""
WebSocket endpoint for real-time job status streaming.

Client connects to: ws://host/api/analyses/{analysis_id}/ws?token=<jwt>

Auth is via query param because WebSocket doesn't support Authorization headers
in the browser API. The JWT is validated the same way as the REST endpoints.

Flow:
  1. Client connects, token is validated, ownership is checked.
  2. Server sends the current job_logs as a "snapshot" message.
  3. Server subscribes to the Redis pub/sub channel for this analysis.
  4. Each progress event from the pipeline is forwarded as JSON.
  5. On terminal event (complete/failed/cancelled), server sends it and closes.
  6. Client can send a JSON message with {"action": "ping"} to keep alive.
"""
import json
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import settings
from app.database import async_session_maker
from app.models import Analysis, Project, JobLog, User
from app.pipeline.progress import subscribe_progress

logger = logging.getLogger(__name__)

router = APIRouter()

# Pipeline steps in order, with display names and weight for progress calc.
# Weight reflects approximate relative duration.
PIPELINE_STEPS = [
    {"key": "upload_received", "label": "Upload Received", "weight": 0.02},
    {"key": "vcf_parsing", "label": "VCF Parsing", "weight": 0.08},
    {"key": "variant_storage", "label": "Storing Variants", "weight": 0.05},
    {"key": "peptide_generation", "label": "Peptide Generation", "weight": 0.10},
    {"key": "mhc_prediction", "label": "MHC Binding Prediction", "weight": 0.40},
    {"key": "scoring", "label": "Immunogenicity Scoring", "weight": 0.15},
    {"key": "ranking", "label": "Ranking & Selection", "weight": 0.05},
    {"key": "results_storage", "label": "Storing Results", "weight": 0.10},
    {"key": "done", "label": "Complete", "weight": 0.05},
]

# Precompute cumulative progress for each step completing
_STEP_KEYS = [s["key"] for s in PIPELINE_STEPS]
_CUMULATIVE = {}
_running = 0.0
for s in PIPELINE_STEPS:
    _running += s["weight"]
    _CUMULATIVE[s["key"]] = round(_running, 3)


def progress_for_step(step: str, status: str) -> float:
    """
    Calculate progress percentage (0-1) based on step and status.
    "running" -> start of step, "complete" -> end of step.
    """
    if step not in _STEP_KEYS:
        return 0.0
    idx = _STEP_KEYS.index(step)
    if status == "complete":
        return _CUMULATIVE[step]
    elif status == "running":
        # halfway between previous complete and this complete
        prev = _CUMULATIVE[_STEP_KEYS[idx - 1]] if idx > 0 else 0.0
        return round((prev + _CUMULATIVE[step]) / 2, 3)
    return 0.0


async def _authenticate_ws(token: str) -> int | None:
    """
    Validate JWT and return user_id, or None if invalid.
    Lightweight check -- no DB hit for the user object itself,
    just decode the token. Ownership check happens separately.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        user_id = payload.get("user_id")
        return user_id
    except JWTError:
        return None


async def _check_ownership(user_id: int, analysis_id: int, db: AsyncSession) -> bool:
    """Verify the user owns the analysis's project."""
    stmt = (
        select(Analysis, Project)
        .join(Project, Analysis.project_id == Project.id)
        .where(Analysis.id == analysis_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        return False
    _, project = row
    return project.user_id == user_id


async def _get_snapshot(analysis_id: int, db: AsyncSession) -> dict:
    """
    Build a snapshot of current status for initial WS message.
    Includes analysis status, all job logs, and step definitions.
    """
    # Get analysis status
    stmt = select(Analysis).where(Analysis.id == analysis_id)
    result = await db.execute(stmt)
    analysis = result.scalar_one_or_none()

    # Get job logs
    log_stmt = (
        select(JobLog)
        .where(JobLog.analysis_id == analysis_id)
        .order_by(JobLog.timestamp)
    )
    log_result = await db.execute(log_stmt)
    logs = log_result.scalars().all()

    # Build step status map from logs
    step_status = {}
    for log in logs:
        step_status[log.step] = {
            "status": log.status,
            "message": log.message,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        }

    # Calculate current progress
    current_progress = 0.0
    for log in logs:
        p = progress_for_step(log.step, log.status)
        if p > current_progress:
            current_progress = p

    return {
        "type": "snapshot",
        "analysis_id": analysis_id,
        "analysis_status": analysis.status if analysis else "unknown",
        "progress_pct": round(current_progress, 3),
        "steps": PIPELINE_STEPS,
        "step_status": step_status,
        "job_logs": [
            {
                "step": log.step,
                "status": log.status,
                "message": log.message,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            }
            for log in logs
        ],
    }


@router.websocket("/{analysis_id}/ws")
async def analysis_ws(
    websocket: WebSocket,
    analysis_id: int,
    token: str = Query(...),
):
    """
    WebSocket endpoint for real-time analysis progress.

    Query params:
      - token: JWT Bearer token (same as used for REST API)

    Messages sent to client:
      - snapshot: initial state with all logs and step definitions
      - progress: real-time step updates from pipeline
      - error: auth/connection errors

    Client can send:
      - {"action": "ping"} -> server replies {"type": "pong"}
    """
    # Auth check before accept. If auth fails, accept then immediately close
    # with an error. (Closing before accept doesn't deliver custom close codes
    # to most browser WebSocket clients.)
    user_id = await _authenticate_ws(token)
    if user_id is None:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": "Invalid or expired token"})
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    # Ownership check
    async with async_session_maker() as db:
        owns = await _check_ownership(user_id, analysis_id, db)
        if not owns:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "Not authorized"})
            await websocket.close(code=4003, reason="Not authorized")
            return

        # Accept connection (auth + ownership passed)
        await websocket.accept()

        # Send initial snapshot
        try:
            snapshot = await _get_snapshot(analysis_id, db)
            await websocket.send_json(snapshot)

            # If analysis is already terminal, send and close
            if snapshot["analysis_status"] in ("complete", "failed", "cancelled"):
                await websocket.send_json({
                    "type": "terminal",
                    "status": snapshot["analysis_status"],
                    "message": f"Analysis already {snapshot['analysis_status']}",
                })
                await websocket.close()
                return
        except WebSocketDisconnect:
            return

    # Subscribe to Redis pub/sub for live updates
    pubsub = await subscribe_progress(analysis_id)

    try:
        # Two concurrent tasks:
        # 1. Listen for Redis messages and forward to WS
        # 2. Listen for client messages (ping/keepalive)
        async def _relay_redis():
            """Forward Redis pub/sub messages to the WebSocket client."""
            async for raw_message in pubsub.listen():
                if raw_message["type"] != "message":
                    continue
                try:
                    data = json.loads(raw_message["data"])
                    data["type"] = "progress"
                    await websocket.send_json(data)

                    # If terminal event, we're done
                    if data.get("terminal"):
                        return
                except (json.JSONDecodeError, WebSocketDisconnect):
                    return

        async def _handle_client():
            """Handle messages from the client (ping/keepalive)."""
            try:
                while True:
                    raw = await websocket.receive_text()
                    try:
                        msg = json.loads(raw)
                        if msg.get("action") == "ping":
                            await websocket.send_json({"type": "pong"})
                    except json.JSONDecodeError:
                        pass
            except WebSocketDisconnect:
                pass

        # Run both concurrently, finish when either completes
        redis_task = asyncio.create_task(_relay_redis())
        client_task = asyncio.create_task(_handle_client())

        done, pending = await asyncio.wait(
            [redis_task, client_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        logger.debug(f"WS client disconnected for analysis {analysis_id}")
    except Exception as e:
        logger.error(f"WS error for analysis {analysis_id}: {e}")
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
        try:
            await websocket.close()
        except Exception:
            pass
