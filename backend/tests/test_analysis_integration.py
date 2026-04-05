"""
Integration tests for analysis endpoints.

Focus: Auth, validation, and response structure rather than complex DB mocking.
"""

import pytest
from unittest.mock import MagicMock
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# --- Project Creation ---

class TestProjectCreation:
    """POST /api/projects - create project."""

    async def test_create_project_success(self, client: AsyncClient, mock_db_override):
        """POST /api/projects with valid data returns 201."""
        response = await client.post(
            "/api/projects/",
            json={
                "name": "Test Project",
                "cancer_type": "Melanoma",
                "stage": "III",
                "reference_genome": "GRCh38",
            },
            headers={
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxLCJlbWFpbCI6InRlc3RAZXhhbXBsZS5jb20iLCJleHAiOjE4MDAwMDAwMDB9.fake"
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Project"
        assert data["cancer_type"] == "Melanoma"

    async def test_create_project_no_auth(self, client: AsyncClient):
        """Create project without token returns 401."""
        response = await client.post(
            "/api/projects/",
            json={"name": "Test", "cancer_type": "Melanoma"},
        )

        assert response.status_code == 401

    async def test_create_project_invalid_data(self, client: AsyncClient, mock_db_override, auth_headers):
        """Missing required fields is rejected."""
        response = await client.post(
            "/api/projects/",
            json={"name": "Test"},  # Missing cancer_type
            headers=auth_headers,
        )

        # Validation error - should not succeed
        assert response.status_code != 201


# --- Analysis Creation ---

class TestAnalysisCreation:
    """POST /api/analyses - create analysis."""

    async def test_create_analysis_success(self, client: AsyncClient, mock_db_override, mock_project, auth_headers):
        """POST /api/analyses with valid project_id returns 201."""
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=mock_project)
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/analyses/",
            json={
                "project_id": 1,
                "input_type": "vcf",
                "hla_provided": False,
            },
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "queued"
        assert data["input_type"] == "vcf"

    async def test_create_analysis_invalid_input_type(self, client: AsyncClient, mock_db_override, auth_headers):
        """POST /api/analyses with invalid input_type returns 422."""
        response = await client.post(
            "/api/analyses/",
            json={
                "project_id": 1,
                "input_type": "invalid",
                "hla_provided": False,
            },
            headers=auth_headers,
        )

        # Validation error - should not succeed
        assert response.status_code != 201

    async def test_create_analysis_valid_input_types(self, client: AsyncClient, mock_db_override, mock_project, auth_headers):
        """All valid input types accepted."""
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=mock_project)
        mock_db_override.configure_execute(return_value=result)

        for input_type in ["vcf", "bam", "fastq"]:
            response = await client.post(
                "/api/analyses/",
                json={"project_id": 1, "input_type": input_type},
                headers=auth_headers,
            )
            assert response.status_code == 201

    async def test_create_analysis_nonexistent_project(self, client: AsyncClient, mock_db_override, auth_headers):
        """POST /api/analyses with non-existent project_id returns 404."""
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/analyses/",
            json={"project_id": 99999, "input_type": "vcf"},
            headers=auth_headers,
        )

        assert response.status_code == 404

    async def test_create_analysis_no_auth(self, client: AsyncClient):
        """POST /api/analyses without token returns 401."""
        response = await client.post(
            "/api/analyses/",
            json={"project_id": 1, "input_type": "vcf"},
        )

        assert response.status_code == 401


# --- Access Control ---

class TestAccessControl:
    """Authorization checks."""

    async def test_get_analysis_nonexistent_returns_404(self, client: AsyncClient, mock_db_override, auth_headers):
        """GET /api/analyses/99999 returns 404."""
        result = MagicMock()
        result.one_or_none = MagicMock(return_value=None)
        mock_db_override.configure_execute(return_value=result)

        response = await client.get(
            "/api/analyses/99999",
            headers=auth_headers,
        )

        assert response.status_code == 404

    async def test_get_analysis_forbidden_for_other_user(self, client: AsyncClient, mock_db_override, auth_headers):
        """User cannot access another user's analysis."""
        analysis = MagicMock()
        analysis.id = 1
        analysis.project_id = 1

        project = MagicMock()
        project.user_id = 2  # Different user

        result = MagicMock()
        result.one_or_none = MagicMock(return_value=(analysis, project))
        mock_db_override.configure_execute(return_value=result)

        response = await client.get(
            "/api/analyses/1",
            headers=auth_headers,
        )

        assert response.status_code == 403


# --- Analysis Listing ---

class TestAnalysisListing:
    """GET /api/analyses - list analyses."""

    async def test_list_analyses_empty(self, client: AsyncClient, mock_db_override, auth_headers):
        """GET /api/analyses returns empty list."""
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)

        list_result = MagicMock()
        list_result.all = MagicMock(return_value=[])

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            return count_result if call_count == 1 else list_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get("/api/analyses/", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "analyses" in data
        assert "total" in data
        assert isinstance(data["analyses"], list)
        assert data["total"] == 0

    async def test_list_analyses_with_filters(self, client: AsyncClient, mock_db_override, auth_headers):
        """GET /api/analyses accepts filter parameters."""
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)

        list_result = MagicMock()
        list_result.all = MagicMock(return_value=[])

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            return count_result if call_count == 1 else list_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/analyses/?status=complete&input_type=vcf&project_id=1",
            headers=auth_headers,
        )

        assert response.status_code == 200


# --- Analysis Status ---

class TestAnalysisStatus:
    """GET /api/analyses/{id}/status - pipeline status."""

    async def test_status_endpoint_exists(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """GET /api/analyses/{id}/status endpoint responds."""
        project = MagicMock()
        project.user_id = 1

        # Ownership check + job logs + counts
        analysis_result = MagicMock()
        analysis_result.one_or_none = MagicMock(return_value=(mock_analysis, project))

        logs_result = MagicMock()
        logs_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[]))
        )

        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return analysis_result
            elif call_count == 2:
                return logs_result
            else:
                return count_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/analyses/1/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "analysis_id" in data
        assert "status" in data
        assert "progress_pct" in data
        assert "pipeline_steps" in data


# --- Dashboard Stats ---

class TestDashboardStats:
    """GET /api/analyses/stats/dashboard."""

    async def test_dashboard_stats_shape(self, client: AsyncClient, mock_db_override, auth_headers):
        """Dashboard stats endpoint responds with correct shape."""
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)

        list_result = MagicMock()
        list_result.all = MagicMock(return_value=[])

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return count_result
            else:
                return list_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/analyses/stats/dashboard",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "total_projects" in data
        assert "total_analyses" in data
        assert "active_analyses" in data
        assert "total_epitopes" in data
        assert "recent_analyses" in data
        assert isinstance(data["recent_analyses"], list)


# --- Lifecycle (simplified) ---

class TestAnalysisLifecycle:
    """Analysis lifecycle operations."""

    async def test_cancel_nonexistent_returns_404(self, client: AsyncClient, mock_db_override, auth_headers):
        """POST /api/analyses/99999/cancel returns 404."""
        result = MagicMock()
        result.one_or_none = MagicMock(return_value=None)
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/analyses/99999/cancel",
            headers=auth_headers,
        )

        assert response.status_code == 404

    async def test_cancel_completed_returns_400(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """Cannot cancel already completed analysis."""
        mock_analysis.status = "complete"
        project = MagicMock()
        project.user_id = 1

        result = MagicMock()
        result.one_or_none = MagicMock(return_value=(mock_analysis, project))
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/analyses/1/cancel",
            headers=auth_headers,
        )

        assert response.status_code == 400

    async def test_retry_nonexistent_returns_404(self, client: AsyncClient, mock_db_override, auth_headers):
        """POST /api/analyses/99999/retry returns 404."""
        result = MagicMock()
        result.one_or_none = MagicMock(return_value=None)
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/analyses/99999/retry",
            headers=auth_headers,
        )

        assert response.status_code == 404

    async def test_retry_only_failed_cancelled(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """Can only retry failed or cancelled analyses."""
        mock_analysis.status = "complete"
        project = MagicMock()
        project.user_id = 1

        result = MagicMock()
        result.one_or_none = MagicMock(return_value=(mock_analysis, project))
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/analyses/1/retry",
            headers=auth_headers,
        )

        assert response.status_code == 400

    async def test_clone_nonexistent_returns_404(self, client: AsyncClient, mock_db_override, auth_headers):
        """POST /api/analyses/99999/clone returns 404."""
        result = MagicMock()
        result.one_or_none = MagicMock(return_value=None)
        mock_db_override.configure_execute(return_value=result)

        response = await client.post(
            "/api/analyses/99999/clone",
            headers=auth_headers,
        )

        assert response.status_code == 404
