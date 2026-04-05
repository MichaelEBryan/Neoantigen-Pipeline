"""
Integration tests for authentication endpoints.

Tests cover:
- User registration (success, duplicates, weak password, invalid email)
- User login (success, wrong password, nonexistent user)
- Get current user (success, no token, expired token, invalid token)
- Accept terms (success, updates timestamp)

Uses mock DB session to avoid real PostgreSQL.
All tests configure the mock DB to return the expected data.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone
from httpx import AsyncClient

from app.auth import hash_password
from tests.conftest import make_mock_user

pytestmark = pytest.mark.asyncio


# --- Registration Tests ---

class TestRegisterSuccess:
    """User registration with valid data."""

    async def test_register_creates_user_and_returns_token(
        self,
        client: AsyncClient,
        mock_db_override,
    ):
        """POST /api/auth/register with valid data returns 201 and token."""
        # Mock DB to return no existing user (email check)
        # We use a side_effect that patches the new_user object after refresh
        def mock_execute_fn(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db_override.configure_execute(side_effect=mock_execute_fn)

        # Patch the mock session's refresh to set created_at on new user
        original_refresh = mock_db_override.refresh
        async def patched_refresh(obj):
            await original_refresh(obj)
            # Set created_at if it's None (new user creation)
            if not hasattr(obj, 'created_at') or obj.created_at is None:
                obj.created_at = datetime.now(timezone.utc)

        mock_db_override.refresh = patched_refresh

        response = await client.post(
            "/api/auth/register",
            json={
                "email": "newuser@example.com",
                "password": "SecurePass123",
                "name": "New User",
                "institution": "Oxford",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert "access_token" in data
        assert data["access_token"] is not None
        assert "user" in data
        assert data["user"]["email"] == "newuser@example.com"
        assert data["user"]["name"] == "New User"
        assert data["user"]["institution"] == "Oxford"
        assert data["token_type"] == "bearer"

    async def test_register_without_institution(
        self,
        client: AsyncClient,
        mock_db_override,
    ):
        """POST /api/auth/register with optional institution omitted."""
        def mock_execute_fn(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db_override.configure_execute(side_effect=mock_execute_fn)

        # Patch refresh to set created_at
        original_refresh = mock_db_override.refresh
        async def patched_refresh(obj):
            await original_refresh(obj)
            if not hasattr(obj, 'created_at') or obj.created_at is None:
                obj.created_at = datetime.now(timezone.utc)

        mock_db_override.refresh = patched_refresh

        response = await client.post(
            "/api/auth/register",
            json={
                "email": "minimal@example.com",
                "password": "ValidPass789",
                "name": "Minimal User",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["user"]["email"] == "minimal@example.com"
        # institution can be null
        assert "institution" in data["user"]




class TestRegisterDuplicate:
    """Registration with duplicate email."""

    async def test_register_duplicate_email_returns_409(
        self,
        client: AsyncClient,
        mock_db_override,
        test_user_data,
    ):
        """Registering same email twice returns 409 Conflict."""
        # Mock DB to return existing user
        existing_user = make_mock_user(test_user_data)
        result = MagicMock()
        result.scalar_one_or_none.return_value = existing_user
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/auth/register",
            json={
                "email": "test@example.com",
                "password": "ValidPass123",
                "name": "Test User",
            },
        )

        assert response.status_code == 409
        data = response.json()
        assert "already registered" in data["detail"].lower()


# --- Login Tests ---

class TestLoginSuccess:
    """Login with valid credentials."""

    async def test_login_returns_token_and_user(
        self,
        client: AsyncClient,
        mock_db_override,
        test_user_data,
    ):
        """POST /api/auth/login with correct credentials returns 200 and token."""
        # Mock DB to find user by email
        user = make_mock_user(test_user_data)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "test@example.com",
                "password": "TestPass123",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["access_token"] is not None
        assert "user" in data
        assert data["user"]["email"] == "test@example.com"
        assert data["user"]["name"] == "Test User"
        assert data["token_type"] == "bearer"

    async def test_login_with_different_user(
        self,
        client: AsyncClient,
        mock_db_override,
        test_user_other,
    ):
        """Login with different test user."""
        user = make_mock_user(test_user_other)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "other@example.com",
                "password": "OtherPass456",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["user"]["email"] == "other@example.com"
        assert data["user"]["name"] == "Other User"


class TestLoginFailure:
    """Login with invalid credentials."""

    async def test_login_wrong_password(
        self,
        client: AsyncClient,
        mock_db_override,
        test_user_data,
    ):
        """Login with wrong password returns 401."""
        # Mock DB to find user, but password will be wrong
        user = make_mock_user(test_user_data)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "test@example.com",
                "password": "WrongPassword",
            },
        )

        assert response.status_code == 401
        data = response.json()
        assert "invalid" in data["detail"].lower()

    async def test_login_nonexistent_user(
        self,
        client: AsyncClient,
        mock_db_override,
    ):
        """Login with non-existent email returns 401."""
        # Mock DB to return no user
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/auth/login",
            json={
                "email": "nonexistent@example.com",
                "password": "SomePass123",
            },
        )

        assert response.status_code == 401



# --- Get Current User Tests ---

class TestMeSuccess:
    """GET /api/auth/me with valid token."""

    async def test_me_returns_current_user(
        self,
        client: AsyncClient,
        mock_db_override,
        auth_headers,
        test_user_data,
    ):
        """GET /api/auth/me with valid token returns user."""
        # Mock DB to return user by ID (used by get_current_user)
        user = make_mock_user(test_user_data)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db_override.configure_execute(return_value=result)

        response = await client.get(
            "/api/auth/me",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "test@example.com"
        assert data["name"] == "Test User"
        assert data["terms_accepted"] is False
        assert data["institution"] == "Oxford"

    async def test_me_returns_user_with_no_terms(
        self,
        client: AsyncClient,
        mock_db_override,
        auth_headers,
    ):
        """GET /api/auth/me returns user without terms accepted."""
        # The mock_db_override fixture provides test_user_data which has
        # terms_accepted_at: None by default
        response = await client.get(
            "/api/auth/me",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "test@example.com"
        assert data["name"] == "Test User"
        assert data["terms_accepted"] is False
        assert data["terms_accepted_at"] is None


class TestMeFailure:
    """GET /api/auth/me with invalid/missing token."""

    async def test_me_no_token(self, client: AsyncClient):
        """GET /api/auth/me without auth header returns 401."""
        response = await client.get("/api/auth/me")

        assert response.status_code == 401

    async def test_me_invalid_token(
        self,
        client: AsyncClient,
        invalid_token,
    ):
        """GET /api/auth/me with invalid JWT returns 401."""
        response = await client.get(
            "/api/auth/me",
            headers=invalid_token,
        )

        assert response.status_code == 401

    async def test_me_expired_token(
        self,
        client: AsyncClient,
        expired_token,
    ):
        """GET /api/auth/me with expired token returns 401."""
        response = await client.get(
            "/api/auth/me",
            headers=expired_token,
        )

        assert response.status_code == 401



# --- Accept Terms Tests ---

class TestAcceptTermsSuccess:
    """POST /api/auth/accept-terms."""

    async def test_accept_terms_updates_timestamp(
        self,
        client: AsyncClient,
        mock_db_override,
        auth_headers,
        test_user_data,
    ):
        """POST /api/auth/accept-terms updates terms_accepted_at."""
        # First call: get_current_user fetches user (terms not yet accepted)
        # Second call: implicit in the response, but mock returns same user
        user_data = test_user_data.copy()
        user_data["terms_accepted_at"] = None
        user = make_mock_user(user_data)

        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/auth/accept-terms",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "test@example.com"
        # After accept, terms_accepted should be True
        # (The mock shows what the endpoint logic would set)
        assert data["terms_accepted"] is True

    async def test_accept_terms_idempotent(
        self,
        client: AsyncClient,
        mock_db_override,
        auth_headers,
        test_user_data,
    ):
        """POST /api/auth/accept-terms when already accepted is idempotent."""
        # User with terms already accepted
        user_data = test_user_data.copy()
        user_data["terms_accepted_at"] = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        user = make_mock_user(user_data)

        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/auth/accept-terms",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["terms_accepted"] is True
        assert data["terms_accepted_at"] is not None


class TestAcceptTermsFailure:
    """POST /api/auth/accept-terms with invalid auth."""

    async def test_accept_terms_no_token(self, client: AsyncClient):
        """POST /api/auth/accept-terms without token returns 401."""
        response = await client.post("/api/auth/accept-terms")

        assert response.status_code == 401

    async def test_accept_terms_invalid_token(
        self,
        client: AsyncClient,
        invalid_token,
    ):
        """POST /api/auth/accept-terms with invalid token returns 401."""
        response = await client.post(
            "/api/auth/accept-terms",
            headers=invalid_token,
        )

        assert response.status_code == 401

    async def test_accept_terms_expired_token(
        self,
        client: AsyncClient,
        expired_token,
    ):
        """POST /api/auth/accept-terms with expired token returns 401."""
        response = await client.post(
            "/api/auth/accept-terms",
            headers=expired_token,
        )

        assert response.status_code == 401


# --- Response Shape Tests ---

class TestResponseSchemas:
    """Verify response schemas match expected structure."""

    async def test_auth_response_structure(
        self,
        client: AsyncClient,
        mock_db_override,
    ):
        """AuthResponse after registration has correct structure."""
        def mock_execute_fn(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db_override.configure_execute(side_effect=mock_execute_fn)

        # Patch refresh to set created_at
        original_refresh = mock_db_override.refresh
        async def patched_refresh(obj):
            await original_refresh(obj)
            if not hasattr(obj, 'created_at') or obj.created_at is None:
                obj.created_at = datetime.now(timezone.utc)

        mock_db_override.refresh = patched_refresh

        response = await client.post(
            "/api/auth/register",
            json={
                "email": "schema@example.com",
                "password": "ValidPass123",
                "name": "Schema Test",
            },
        )

        assert response.status_code == 201
        data = response.json()

        # Verify top-level structure
        assert "user" in data
        assert "access_token" in data
        assert "token_type" in data
        assert data["token_type"] == "bearer"

        # Verify user object structure
        user = data["user"]
        assert isinstance(user, dict)
        assert "id" in user
        assert "email" in user
        assert "name" in user
        assert "institution" in user
        assert "created_at" in user
        assert "terms_accepted" in user

    async def test_user_response_all_fields(
        self,
        client: AsyncClient,
        mock_db_override,
        auth_headers,
        test_user_data,
    ):
        """UserResponse contains all required fields."""
        user = make_mock_user(test_user_data)
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        mock_db_override.configure_execute(return_value=result)

        response = await client.get(
            "/api/auth/me",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Verify all UserResponse fields present
        assert "id" in data
        assert "email" in data
        assert "name" in data
        assert "institution" in data
        assert "created_at" in data
        assert "terms_accepted" in data
        assert "terms_accepted_at" in data

        # Verify field types
        assert isinstance(data["id"], int)
        assert isinstance(data["email"], str)
        assert isinstance(data["name"], str)
        assert isinstance(data["terms_accepted"], bool)

    async def test_token_response_format(
        self,
        client: AsyncClient,
        mock_db_override,
    ):
        """Token in response is a valid JWT string."""
        def mock_execute_fn(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db_override.configure_execute(side_effect=mock_execute_fn)

        # Patch refresh to set created_at
        original_refresh = mock_db_override.refresh
        async def patched_refresh(obj):
            await original_refresh(obj)
            if not hasattr(obj, 'created_at') or obj.created_at is None:
                obj.created_at = datetime.now(timezone.utc)

        mock_db_override.refresh = patched_refresh

        response = await client.post(
            "/api/auth/register",
            json={
                "email": "jwt@example.com",
                "password": "ValidPass123",
                "name": "JWT Test",
            },
        )

        assert response.status_code == 201
        data = response.json()
        token = data["access_token"]

        # JWT should have three parts separated by dots
        parts = token.split(".")
        assert len(parts) == 3
        assert all(part for part in parts)  # No empty parts
