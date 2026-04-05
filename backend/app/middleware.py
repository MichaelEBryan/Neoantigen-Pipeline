import asyncio
import json
import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


# Request ID context variable - accessible by loggers and other modules
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


# ============================================================================
# JSON Logging Setup
# ============================================================================

class JsonFormatter(logging.Formatter):
    """Custom formatter that outputs structured JSON logs."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.name,
        }

        # Add request_id if available in context
        req_id = request_id_var.get()
        if req_id:
            log_obj["request_id"] = req_id

        # Add exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


def setup_logging(level: str = "INFO") -> None:
    """
    Configure root logger with JSON formatter.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add new handler with JSON formatter
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(JsonFormatter())
    root_logger.addHandler(console_handler)


# ============================================================================
# Request ID Middleware
# ============================================================================

class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that generates a unique request ID for each request.

    - Generates UUID4 request_id
    - Stores in ContextVar for access by loggers
    - Returns in X-Request-ID response header
    """

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        """Generate request ID and attach to request."""
        # Generate or extract request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request_id_var.set(request_id)

        # Call next middleware/endpoint
        response = await call_next(request)

        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id

        return response


# ============================================================================
# Request/Response Logging Middleware
# ============================================================================

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs all requests and responses with details.

    Logs: method, path, status_code, duration_ms, request_id
    Skips: /health, /docs endpoints
    Log levels: INFO for 2xx/3xx, WARNING for 4xx, ERROR for 5xx
    """

    # Paths to skip logging
    SKIP_PATHS = {"/health", "/docs", "/openapi.json", "/favicon.ico"}

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        """Log request and response."""
        # Check if path should be logged
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        logger = logging.getLogger(__name__)
        start_time = time.time()

        # Call next middleware/endpoint
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000

        # Determine log level based on status code
        if response.status_code >= 500:
            log_level = logging.ERROR
        elif response.status_code >= 400:
            log_level = logging.WARNING
        else:
            log_level = logging.INFO

        # Log the request
        request_id = request_id_var.get()
        logger.log(
            log_level,
            f"{request.method} {request.url.path} {response.status_code} {duration_ms:.2f}ms",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        return response


# ============================================================================
# Rate Limiter Middleware
# ============================================================================

@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    requests_per_minute: int = 60
    burst: int = 10
    login_per_minute: int = 5


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory token bucket rate limiter.

    Features:
    - Per-IP rate limiting
    - Per-user rate limiting (from JWT)
    - Stricter limit for /api/auth/login endpoint
    - Thread-safe using asyncio.Lock
    - Cleanup stale entries every 5 minutes
    """

    def __init__(self, app: ASGIApp, config: Optional[RateLimitConfig] = None):
        """Initialize rate limiter."""
        super().__init__(app)
        self.config = config or RateLimitConfig()
        self.buckets: dict[str, dict] = {}  # key -> {tokens, last_update}
        self.lock = asyncio.Lock()
        self.cleanup_task: Optional[asyncio.Task] = None
        self._cleanup_started = False

    def reset(self) -> None:
        """Clear all rate limit buckets. Used in tests."""
        self.buckets.clear()

    async def _ensure_cleanup_task(self) -> None:
        """Ensure cleanup task is running (lazy init)."""
        if not self._cleanup_started:
            self._cleanup_started = True
            self.cleanup_task = asyncio.create_task(self._cleanup_stale_entries())

    async def _cleanup_stale_entries(self) -> None:
        """Periodically clean up stale rate limit entries."""
        while True:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes
                async with self.lock:
                    now = time.time()
                    # Remove entries older than 10 minutes
                    stale_keys = [
                        k
                        for k, v in self.buckets.items()
                        if (now - v.get("last_update", now)) > 600
                    ]
                    for key in stale_keys:
                        del self.buckets[key]
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _check_rate_limit(
        self, key: str, max_requests: int, window_minutes: int = 1
    ) -> bool:
        """
        Check if request is allowed under rate limit.

        Returns True if allowed, False if rate limited.
        Uses token bucket algorithm.
        """
        async with self.lock:
            now = time.time()

            # Initialize bucket if not exists
            if key not in self.buckets:
                self.buckets[key] = {
                    "tokens": max_requests,
                    "last_update": now,
                }

            bucket = self.buckets[key]

            # Refill tokens based on time elapsed
            time_passed = now - bucket["last_update"]
            tokens_per_second = max_requests / (window_minutes * 60)
            bucket["tokens"] = min(
                max_requests,
                bucket["tokens"] + (time_passed * tokens_per_second),
            )
            bucket["last_update"] = now

            # Check if request can proceed
            if bucket["tokens"] >= 1:
                bucket["tokens"] -= 1
                return True

            return False

    def _extract_user_id_from_request(self, request: Request) -> Optional[str]:
        """Extract user_id from JWT token in Authorization header."""
        try:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return None

            # We don't validate here, just extract the subject from the token
            # If JWT is invalid, we fall back to IP-based limiting
            from jose import jwt
            from app.config import settings

            token = auth_header.split(" ")[1]
            payload = jwt.get_unverified_claims(token)
            return payload.get("sub")  # sub is typically user_id
        except Exception:
            return None

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        """Check rate limits and potentially block request."""
        logger = logging.getLogger(__name__)

        # Ensure cleanup task is running (lazy initialization)
        await self._ensure_cleanup_task()

        # Get client IP
        client_ip = request.client.host if request.client else "unknown"

        # Check if this is a login endpoint (stricter limit)
        is_login_endpoint = request.url.path == "/api/auth/login"
        limit = self.config.login_per_minute if is_login_endpoint else self.config.requests_per_minute

        # Try user-based limiting for authenticated endpoints
        user_id = self._extract_user_id_from_request(request)
        if user_id:
            rate_key = f"user:{user_id}"
        else:
            rate_key = f"ip:{client_ip}"

        # Check rate limit
        allowed = await self._check_rate_limit(rate_key, limit)

        if not allowed:
            logger.warning(f"Rate limit exceeded for {rate_key}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "Rate limit exceeded",
                    "request_id": request_id_var.get(),
                },
                headers={"Retry-After": "60"},
            )

        # Call next middleware/endpoint
        return await call_next(request)


# ============================================================================
# Security Headers Middleware
# ============================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds security-related headers to all responses.

    Headers:
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - X-XSS-Protection: 1; mode=block
    - Strict-Transport-Security: max-age=31536000 (only if not localhost)
    - Referrer-Policy: strict-origin-when-cross-origin
    - Cache-Control: no-store
    """

    def __init__(self, app: ASGIApp, is_localhost: bool = False):
        """Initialize with option to skip HSTS on localhost."""
        super().__init__(app)
        self.is_localhost = is_localhost

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        """Add security headers to response."""
        response = await call_next(request)

        # Set security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"

        # Only add HSTS if not localhost
        if not self.is_localhost:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"

        return response


# ============================================================================
# Global Exception Handlers
# ============================================================================

async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions without exposing stack traces."""
    logger = logging.getLogger(__name__)
    logger.error(f"Unhandled exception: {exc}", exc_info=exc)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "request_id": request_id_var.get(),
        },
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle HTTP exceptions from FastAPI."""
    from fastapi import HTTPException

    if not isinstance(exc, HTTPException):
        return await unhandled_exception_handler(request, exc)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "request_id": request_id_var.get(),
        },
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle Pydantic validation exceptions."""
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        return await unhandled_exception_handler(request, exc)

    # Extract field-level errors
    errors = []
    for error in exc.errors():
        errors.append({
            "field": ".".join(str(x) for x in error["loc"][1:]),
            "message": error["msg"],
            "type": error["type"],
        })

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Validation error",
            "request_id": request_id_var.get(),
            "errors": errors,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app."""
    from fastapi.exceptions import RequestValidationError
    from fastapi import HTTPException

    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
