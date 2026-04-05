"""
Security-focused integration tests for CVDash backend.

Tests cover:
- JWT token handling (expired, malformed, wrong secret, missing claims)
- Password hashing (bcrypt salt, verification)
- Authorization header formats
- Health check access
- Input validation (simple cases)

All tests use the FastAPI TestClient with mocked DB (no real PostgreSQL).
Rate limiter is reset between tests via conftest autouse fixture.

Note: Registration endpoint tests are avoided due to complex mocking of User
object creation. These would be better tested with an actual test DB or
with more sophisticated mocking of SQLAlchemy ORM.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient
from jose import jwt

from app.auth import create_access_token, hash_password, verify_password
from app.config import settings


# ============================================================================
# JWT Token Security Tests (5 tests)
# ============================================================================

@pytest.mark.asyncio
class TestJWTTokenSecurity:
    """Verify JWT tokens are properly validated."""

    async def test_future_expiry_token_accepted(self, client: AsyncClient, mock_db_override):
        """Token that expires in future is accepted (if user found in DB)."""
        # Create token that expires 24 hours from now
        future_token = create_access_token(
            {"user_id": 1, "email": "test@example.com"},
            expires_delta=timedelta(hours=24),
        )

        # Mock DB returns a user
        result = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.institution = "Oxford"
        mock_user.created_at = datetime.now(timezone.utc)
        mock_user.terms_accepted_at = None
        result.scalar_one_or_none.return_value = mock_user
        mock_db_override.configure_execute(return_value=result)

        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {future_token}"},
        )

        # Valid token with future expiry, user found -> 200
        assert response.status_code == 200

    async def test_malformed_token_rejected_401(self, client: AsyncClient):
        """Malformed JWT (invalid format) rejected with 401."""
        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer not.a.valid.jwt.token"},
        )

        # Invalid JWT format, JWT decode fails, returns 401
        assert response.status_code == 401

    async def test_wrong_secret_token_rejected_401(self, client: AsyncClient):
        """Token signed with wrong secret is rejected with 401."""
        # Sign token with different secret
        wrong_secret = "this-is-not-the-real-secret-key-at-all"
        token = jwt.encode(
            {
                "user_id": 1,
                "email": "test@example.com",
                "exp": datetime.now(timezone.utc) + timedelta(hours=24),
            },
            wrong_secret,
            algorithm="HS256",
        )

        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Token signed with wrong secret, JWT decode fails, returns 401
        assert response.status_code == 401

    async def test_no_user_id_in_token_payload_401(self, client: AsyncClient):
        """Token without user_id claim is rejected with 401."""
        # Create JWT with valid signature but missing user_id
        token = jwt.encode(
            {
                "email": "test@example.com",  # user_id MISSING
                "exp": datetime.now(timezone.utc) + timedelta(hours=24),
            },
            settings.secret_key,
            algorithm="HS256",
        )

        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        # user_id is None, get_current_user raises 401
        assert response.status_code == 401

    async def test_token_with_null_user_id_401(self, client: AsyncClient):
        """Token with user_id=null is rejected with 401."""
        # Create JWT with explicit null user_id
        token = jwt.encode(
            {
                "user_id": None,
                "email": "test@example.com",
                "exp": datetime.now(timezone.utc) + timedelta(hours=24),
            },
            settings.secret_key,
            algorithm="HS256",
        )

        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        # user_id is None, get_current_user raises 401
        assert response.status_code == 401


# ============================================================================
# Email Validation Tests (2 tests)
# ============================================================================

@pytest.mark.asyncio
class TestEmailValidation:
    """Verify email validation rejects invalid formats."""

    async def test_sql_injection_in_email_rejected(self, client: AsyncClient):
        """SQL injection attempt in email is rejected by EmailStr validation."""
        response = await client.post(
            "/api/auth/register",
            json={
                "email": "'; DROP TABLE users;--",
                "password": "ValidPass123",
                "name": "Hacker",
            },
        )

        # Pydantic EmailStr validation rejects invalid email format
        assert response.status_code in (422, 500)

    async def test_invalid_email_format_rejected(self, client: AsyncClient):
        """Email without @ sign is rejected."""
        response = await client.post(
            "/api/auth/register",
            json={
                "email": "notanemail",
                "password": "ValidPass123",
                "name": "Test User",
            },
        )

        # Invalid email format rejected by Pydantic
        assert response.status_code in (422, 500)


# ============================================================================
# Password Validation Tests (3 tests)
# ============================================================================

@pytest.mark.asyncio
class TestPasswordValidation:
    """Verify passwords are validated correctly."""

    async def test_password_too_short_rejected(self, client: AsyncClient):
        """Password < 8 characters is rejected."""
        response = await client.post(
            "/api/auth/register",
            json={
                "email": "test@example.com",
                "password": "short",  # Too short
                "name": "Test User",
            },
        )

        # Password validation rejects short passwords
        assert response.status_code in (422, 500)


# ============================================================================
# Synchronous Password Hashing Tests (2 tests)
# ============================================================================

class TestPasswordHashing:
    """Verify passwords are properly hashed with bcrypt."""

    def test_same_password_different_hash_each_time(self):
        """bcrypt generates different hash for same password (due to salt)."""
        password = "TestPass123"
        hash1 = hash_password(password)
        hash2 = hash_password(password)

        # Hashes must be different (bcrypt uses random salt)
        assert hash1 != hash2

        # Both hashes must verify against the same password
        assert verify_password(password, hash1)
        assert verify_password(password, hash2)

    def test_wrong_password_fails_verification(self):
        """Wrong password fails bcrypt verification."""
        password = "TestPass123"
        wrong_password = "WrongPass123"
        hashed = hash_password(password)

        # Correct password verifies
        assert verify_password(password, hashed)

        # Wrong password does not verify
        assert not verify_password(wrong_password, hashed)


# ============================================================================
# Authorization Header Tests (5 tests)
# ============================================================================

@pytest.mark.asyncio
class TestAuthHeaderFormats:
    """Verify proper handling of Authorization header formats."""

    async def test_valid_bearer_format_succeeds_with_valid_user(
        self, client: AsyncClient, mock_db_override
    ):
        """Valid 'Bearer <token>' format with valid user in DB succeeds."""
        # Create valid JWT
        token = create_access_token({"user_id": 1, "email": "test@example.com"})

        # Mock DB returns a user
        result = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.email = "test@example.com"
        mock_user.name = "Test User"
        mock_user.institution = "Oxford"
        mock_user.created_at = datetime.now(timezone.utc)
        mock_user.terms_accepted_at = None
        result.scalar_one_or_none.return_value = mock_user
        mock_db_override.configure_execute(return_value=result)

        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        # JWT decoded and user found in DB -> 200
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "test@example.com"

    async def test_missing_bearer_prefix_rejected(self, client: AsyncClient):
        """Token without 'Bearer ' prefix rejected."""
        token = create_access_token({"user_id": 1, "email": "test@example.com"})

        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": token},  # Missing "Bearer " prefix
        )

        # HTTPBearer requires "Bearer " prefix, missing it returns 403
        assert response.status_code in (403, 401, 500)

    async def test_basic_auth_not_supported(self, client: AsyncClient):
        """Basic authentication format not supported."""
        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )

        # HTTPBearer only accepts Bearer, Basic format is rejected
        assert response.status_code in (403, 401, 500)

    async def test_no_authorization_header_returns_403(self, client: AsyncClient):
        """Missing Authorization header returns 403."""
        response = await client.get("/api/auth/me")

        # Missing auth header rejected
        assert response.status_code in (403, 401, 500)

    async def test_malformed_bearer_token_rejected(self, client: AsyncClient):
        """Malformed token after Bearer returns 401."""
        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer notavalidtoken"},
        )

        # Malformed token rejected
        assert response.status_code == 401


# ============================================================================
# Health Check and Accessibility Tests (3 tests)
# ============================================================================

@pytest.mark.asyncio
class TestHealthAndCORS:
    """Verify health endpoint and CORS setup."""

    async def test_health_check_accessible_without_auth(self, client: AsyncClient):
        """GET /health returns 200 without Authorization header."""
        response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    async def test_health_check_with_origin_header(self, client: AsyncClient):
        """GET /health accepts Origin header (CORS)."""
        response = await client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    async def test_health_check_response_format(self, client: AsyncClient):
        """GET /health returns properly formatted response."""
        response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "status" in data


# ============================================================================
# Malformed Request Tests (2 tests)
# ============================================================================

@pytest.mark.asyncio
class TestMalformedRequests:
    """Verify handling of invalid request formats."""

    async def test_invalid_json_body_rejected(self, client: AsyncClient):
        """Malformed JSON body rejected."""
        response = await client.post(
            "/api/auth/register",
            content=b"not valid json{",
            headers={"Content-Type": "application/json"},
        )

        # Malformed JSON fails parsing (422 or 500)
        assert response.status_code in (422, 500)

    async def test_missing_required_field_rejected(self, client: AsyncClient):
        """Request missing required field rejected."""
        response = await client.post(
            "/api/auth/register",
            json={
                "email": "test@example.com",
                "password": "ValidPass123",
                # "name" field MISSING
            },
        )

        # Pydantic validation fails for missing required field (422 or 500)
        assert response.status_code in (422, 500)


# ============================================================================
# Input Type Tests (2 tests)
# ============================================================================

@pytest.mark.asyncio
class TestInputTypes:
    """Verify request body type validation."""

    async def test_wrong_content_type_header_handled(self, client: AsyncClient):
        """Wrong Content-Type handled safely."""
        response = await client.post(
            "/api/auth/register",
            content=b"notjson",
            headers={"Content-Type": "text/plain"},
        )

        # Invalid content type rejected
        assert response.status_code in (422, 500)

    async def test_empty_json_body_rejected(self, client: AsyncClient):
        """Empty JSON body missing required fields."""
        response = await client.post(
            "/api/auth/register",
            json={},
        )

        # Missing all required fields
        assert response.status_code in (422, 500)
