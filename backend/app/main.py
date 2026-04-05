import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.middleware import (
    setup_logging,
    SecurityHeadersMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    RateLimitMiddleware,
    RateLimitConfig,
    register_exception_handlers,
)
from app.health import router as health_router
from app.routers import analyses, epitopes, auth, projects, uploads, browser, ws, admin, construct, blast, dai, compare, report, annotate, settings
from app.pipeline.progress import cleanup_redis

# Setup JSON logging at module level
setup_logging(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage startup and shutdown events.
    """
    logger.info("Oxford Cancer Vaccine Design backend starting up...")
    yield
    logger.info("Oxford Cancer Vaccine Design backend shutting down...")
    await cleanup_redis()


app = FastAPI(
    title="Oxford Cancer Vaccine Design",
    description="API for Cancer Vaccine Design Tool",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# Register exception handlers before middleware
register_exception_handlers(app)

# Middleware stack (order matters - outermost first)
# Security headers should be outermost to wrap all responses
is_localhost = settings.environment == "development" and "localhost" in settings.allowed_origins
app.add_middleware(SecurityHeadersMiddleware, is_localhost=is_localhost)

# Request ID middleware - must be early so logging can access it
app.add_middleware(RequestIDMiddleware)

# Request/response logging middleware
app.add_middleware(RequestLoggingMiddleware)

# Rate limiting middleware
rate_limit_config = RateLimitConfig(
    requests_per_minute=settings.rate_limit_rpm,
    burst=settings.rate_limit_burst,
    login_per_minute=settings.login_rate_limit_rpm,
)
app.add_middleware(RateLimitMiddleware, config=rate_limit_config)

# CORS middleware (tighter configuration)
# Only allow specified methods and headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins_list(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Include health check router
app.include_router(health_router, tags=["health"])

# Include API routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(analyses.router, prefix="/api/analyses", tags=["analyses"])
app.include_router(epitopes.router, prefix="/api/epitopes", tags=["epitopes"])
app.include_router(uploads.router, prefix="/api/analyses", tags=["uploads"])
app.include_router(browser.router, prefix="/api/analyses", tags=["browser"])
app.include_router(ws.router, prefix="/api/analyses", tags=["websocket"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(construct.router, tags=["construct"])
app.include_router(blast.router, tags=["blast"])
app.include_router(dai.router, tags=["dai"])
app.include_router(compare.router, tags=["compare"])
app.include_router(report.router, tags=["report"])
app.include_router(annotate.router, tags=["annotate"])
app.include_router(settings.router, tags=["settings"])
