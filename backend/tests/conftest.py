"""
Shared fixtures for CVDash integration tests.

Uses unittest.mock to mock DB interactions, avoiding real PostgreSQL connection.
Tests run against the actual FastAPI app with mocked dependencies.

The rate limiter is reset before each test to prevent cross-test 429s.
The get_db dependency is overridden with an async generator that yields a mock session.
"""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.auth import create_access_token, hash_password
from app.database import get_db


# ---------------------------------------------------------------------------
# Rate limiter reset: walk the middleware stack and clear buckets before each test
# ---------------------------------------------------------------------------

def _reset_rate_limiter():
    """Find the RateLimitMiddleware in the app's middleware stack and reset it."""
    from app.middleware import RateLimitMiddleware

    # Starlette wraps middleware in a chain; walk it
    handler = app.middleware_stack
    seen = set()
    while handler is not None and id(handler) not in seen:
        seen.add(id(handler))
        if isinstance(handler, RateLimitMiddleware):
            handler.reset()
            return
        handler = getattr(handler, "app", None)


@pytest.fixture(autouse=True)
def reset_rate_limiter_between_tests():
    """Auto-fixture: reset rate limiter state before every test."""
    _reset_rate_limiter()
    yield
    _reset_rate_limiter()


# Mock external services to avoid connection errors
@pytest.fixture(autouse=True)
def mock_external_services():
    """Mock redis and celery calls to avoid connection errors."""
    # Mock at the import location in the routers
    with patch("app.routers.analyses.get_celery_task_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch("app.routers.analyses.store_celery_task_id", new_callable=AsyncMock) as mock_store:
            # Celery task is imported inside functions, mock at module level
            with patch("app.celery_app.process_vcf_analysis") as mock_celery:
                mock_task = MagicMock()
                mock_task.id = "test-task-id"
                mock_celery.delay.return_value = mock_task
                yield {
                    "get_celery_task_id": mock_get,
                    "store_celery_task_id": mock_store,
                    "process_vcf_analysis": mock_celery,
                }


# ---------------------------------------------------------------------------
# Mock DB session -- mimics async SQLAlchemy session
# ---------------------------------------------------------------------------

class MockAsyncSession:
    """
    Lightweight mock of SQLAlchemy AsyncSession.

    Each test configures what execute() returns via mock_db_session.execute_returns.
    Default: return empty results for everything.
    """

    def __init__(self):
        self._execute_side_effect = None
        self._execute_return = None

    def configure_execute(self, return_value=None, side_effect=None):
        """Configure what execute() returns or raises."""
        self._execute_return = return_value
        self._execute_side_effect = side_effect

    async def execute(self, stmt, *args, **kwargs):
        """Mock execute. Returns a mock result object."""
        if self._execute_side_effect:
            if callable(self._execute_side_effect):
                return self._execute_side_effect(stmt)
            raise self._execute_side_effect

        if self._execute_return is not None:
            return self._execute_return

        # Default: empty results
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalar.return_value = 0
        result.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
        result.one_or_none.return_value = None
        result.all.return_value = []
        return result

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def flush(self):
        pass

    async def refresh(self, obj):
        # Simulate DB setting an ID and created_at on new objects
        if hasattr(obj, "id") and obj.id is None:
            obj.id = 1
        if hasattr(obj, "created_at") and obj.created_at is None:
            obj.created_at = datetime.now(timezone.utc)

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# get_db override -- must be an async generator, same signature as the real one
# ---------------------------------------------------------------------------

_current_mock_session = None


async def _mock_get_db():
    """Async generator override for get_db dependency."""
    yield _current_mock_session


@pytest.fixture
def mock_db_session():
    """Create a fresh mock DB session for this test."""
    return MockAsyncSession()


async def _mock_get_current_user(test_user_data=None):
    """Factory for creating mock get_current_user that doesn't hit DB."""
    async def mock_get_current_user():
        if test_user_data is None:
            data = {
                "id": 1,
                "email": "test@example.com",
                "name": "Test User",
                "institution": "Oxford",
                "hashed_password": hash_password("TestPass123"),
                "created_at": datetime.now(timezone.utc),
                "terms_accepted_at": None,
            }
        else:
            data = test_user_data
        return make_mock_user(data)
    return mock_get_current_user


@pytest.fixture(autouse=False)
def mock_db_override(mock_db_session, test_user_data):
    """
    Override FastAPI's get_db dependency with our mock session.
    Also overrides get_current_user to return test user without DB lookup.

    Usage: include this fixture in any test that hits endpoints needing DB.
    It's NOT autouse because some tests (like pure JWT tests) don't need it.
    """
    global _current_mock_session
    _current_mock_session = mock_db_session
    app.dependency_overrides[get_db] = _mock_get_db

    # Override get_current_user to not hit DB
    from app.auth import get_current_user
    async def _get_current_user_override():
        return make_mock_user(test_user_data)
    app.dependency_overrides[get_current_user] = _get_current_user_override

    yield mock_db_session
    app.dependency_overrides.clear()
    _current_mock_session = None


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_user_data():
    """Test user data dictionary."""
    return {
        "id": 1,
        "email": "test@example.com",
        "name": "Test User",
        "institution": "Oxford",
        "hashed_password": hash_password("TestPass123"),
        "created_at": datetime.now(timezone.utc),
        "terms_accepted_at": None,
    }


@pytest.fixture
def test_user_other():
    """Another test user for cross-user auth tests."""
    return {
        "id": 2,
        "email": "other@example.com",
        "name": "Other User",
        "institution": "Cambridge",
        "hashed_password": hash_password("OtherPass456"),
        "created_at": datetime.now(timezone.utc),
        "terms_accepted_at": None,
    }


# ---------------------------------------------------------------------------
# Auth header fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_headers(test_user_data):
    """JWT authorization headers for test user."""
    token = create_access_token({
        "user_id": test_user_data["id"],
        "email": test_user_data["email"],
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_headers_other(test_user_other):
    """JWT authorization headers for other user."""
    token = create_access_token({
        "user_id": test_user_other["id"],
        "email": test_user_other["email"],
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def expired_token():
    """Create an expired JWT token."""
    token = create_access_token(
        {"user_id": 1, "email": "test@example.com"},
        expires_delta=timedelta(hours=-1),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def invalid_token():
    """Invalid JWT token."""
    return {"Authorization": "Bearer invalid.not.a.real.jwt.token"}


# ---------------------------------------------------------------------------
# Mock ORM objects (for configuring mock_db_session.execute returns)
# ---------------------------------------------------------------------------

def make_mock_user(data: dict):
    """Create a MagicMock that looks like a User ORM object."""
    user = MagicMock()
    user.id = data["id"]
    user.email = data["email"]
    user.name = data["name"]
    user.institution = data.get("institution")
    user.hashed_password = data["hashed_password"]
    user.created_at = data["created_at"]
    user.terms_accepted_at = data.get("terms_accepted_at")
    return user


def make_mock_project(user_id=1, project_id=1, name="Test Project"):
    """Create a MagicMock that looks like a Project ORM object."""
    project = MagicMock()
    project.id = project_id
    project.user_id = user_id
    project.name = name
    project.cancer_type = "Melanoma"
    project.stage = "III"
    project.reference_genome = "GRCh38"
    project.created_at = datetime.now(timezone.utc)
    project.analyses = []
    return project


def make_mock_analysis(project_id=1, analysis_id=1, status="queued"):
    """Create a MagicMock that looks like an Analysis ORM object."""
    analysis = MagicMock()
    analysis.id = analysis_id
    analysis.project_id = project_id
    analysis.status = status
    analysis.input_type = "vcf"
    analysis.hla_provided = False
    analysis.isambard_job_id = None
    analysis.created_at = datetime.now(timezone.utc)
    analysis.completed_at = None
    return analysis


def make_mock_variant(analysis_id=1, variant_id=1):
    """Create a MagicMock that looks like a Variant ORM object."""
    variant = MagicMock()
    variant.id = variant_id
    variant.analysis_id = analysis_id
    variant.chrom = "chr7"
    variant.pos = 140453136
    variant.ref = "A"
    variant.alt = "T"
    variant.gene = "BRAF"
    variant.protein_change = "p.V600E"
    variant.variant_type = "missense"
    variant.vaf = 0.35
    variant.annotation_json = {}
    return variant


def make_mock_epitope(analysis_id=1, variant_id=1, epitope_id=1, rank=1):
    """Create a MagicMock that looks like an Epitope ORM object."""
    variant = make_mock_variant(analysis_id, variant_id)
    epitope = MagicMock()
    epitope.id = epitope_id
    epitope.analysis_id = analysis_id
    epitope.variant_id = variant_id
    epitope.peptide_seq = "LATEKSRWS"
    epitope.peptide_length = 9
    epitope.hla_allele = "HLA-A*02:01"
    epitope.binding_affinity_nm = 25.3
    epitope.presentation_score = 0.89
    epitope.processing_score = 0.72
    epitope.expression_tpm = 45.2
    epitope.immunogenicity_score = 0.82
    epitope.rank = rank
    epitope.explanation_json = {
        "presentation_contribution": 0.267,
        "binding_rank_contribution": 0.225,
        "expression_contribution": 0.124,
        "vaf_contribution": 0.070,
        "mutation_type_contribution": 0.050,
        "processing_contribution": 0.036,
        "iedb_contribution": 0.025,
        "raw_binding_affinity_nm": 25.3,
        "raw_presentation_score": 0.89,
        "raw_processing_score": 0.72,
        "raw_expression_tpm": 45.2,
        "raw_vaf": 0.35,
        "mutation_type": "missense",
    }
    epitope.variant = variant
    return epitope


# ---------------------------------------------------------------------------
# Additional convenience fixtures for tests
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_project():
    """Create a mock project for test_user with ID 1."""
    return make_mock_project(user_id=1, project_id=1, name="Test Project")


@pytest.fixture
def mock_analysis():
    """Create a mock analysis."""
    return make_mock_analysis(project_id=1, analysis_id=1, status="queued")


@pytest.fixture
def mock_variant():
    """Create a mock variant."""
    return make_mock_variant(analysis_id=1, variant_id=1)


@pytest.fixture
def mock_epitope():
    """Create a mock epitope with linked variant."""
    return make_mock_epitope(analysis_id=1, variant_id=1, epitope_id=1, rank=1)


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    """Async HTTP client for testing FastAPI endpoints."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
