"""
Tests for Task 13: History / Projects Page.

Tests the new Pydantic models, route definitions, and response shapes.
No DB or Redis required -- pure unit tests on imports and schemas.
"""
import pytest
from datetime import datetime, timezone


class TestAnalysisListResponse:
    """Test the AnalysisListResponse and extended AnalysisResponse models."""

    def test_analysis_response_has_optional_fields(self):
        from app.routers.analyses import AnalysisResponse
        a = AnalysisResponse(
            id=1,
            project_id=1,
            status="complete",
            input_type="vcf",
            hla_provided=True,
            isambard_job_id=None,
            created_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            # These should default to None
        )
        assert a.project_name is None
        assert a.cancer_type is None
        assert a.variant_count is None
        assert a.epitope_count is None

    def test_analysis_response_with_extras(self):
        from app.routers.analyses import AnalysisResponse
        a = AnalysisResponse(
            id=1,
            project_id=1,
            status="complete",
            input_type="vcf",
            hla_provided=True,
            isambard_job_id=None,
            created_at=datetime.now(timezone.utc),
            completed_at=None,
            project_name="Melanoma Study",
            cancer_type="Melanoma",
            variant_count=5,
            epitope_count=47,
        )
        assert a.project_name == "Melanoma Study"
        assert a.variant_count == 5

    def test_analysis_list_response_shape(self):
        from app.routers.analyses import AnalysisListResponse, AnalysisResponse
        resp = AnalysisListResponse(analyses=[], total=0)
        assert resp.total == 0
        assert resp.analyses == []

    def test_analysis_list_response_with_items(self):
        from app.routers.analyses import AnalysisListResponse, AnalysisResponse
        a = AnalysisResponse(
            id=1,
            project_id=1,
            status="running",
            input_type="bam",
            hla_provided=False,
            isambard_job_id=None,
            created_at=datetime.now(timezone.utc),
            completed_at=None,
        )
        resp = AnalysisListResponse(analyses=[a], total=1)
        assert resp.total == 1
        assert len(resp.analyses) == 1


class TestDashboardStatsResponse:
    """Test the DashboardStatsResponse model."""

    def test_dashboard_stats_shape(self):
        from app.routers.analyses import DashboardStatsResponse
        resp = DashboardStatsResponse(
            total_projects=3,
            total_analyses=12,
            active_analyses=2,
            total_epitopes=150,
            recent_analyses=[],
        )
        assert resp.total_projects == 3
        assert resp.active_analyses == 2
        assert resp.recent_analyses == []

    def test_dashboard_stats_with_recent(self):
        from app.routers.analyses import DashboardStatsResponse, AnalysisResponse
        a = AnalysisResponse(
            id=42,
            project_id=1,
            status="complete",
            input_type="vcf",
            hla_provided=True,
            isambard_job_id=None,
            created_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            project_name="Test Project",
            cancer_type="NSCLC",
            variant_count=8,
            epitope_count=23,
        )
        resp = DashboardStatsResponse(
            total_projects=1,
            total_analyses=1,
            active_analyses=0,
            total_epitopes=23,
            recent_analyses=[a],
        )
        assert len(resp.recent_analyses) == 1
        assert resp.recent_analyses[0].id == 42


class TestCloneResponse:
    """Test the CloneResponse model."""

    def test_clone_response_shape(self):
        from app.routers.analyses import CloneResponse
        resp = CloneResponse(
            analysis_id=99,
            status="queued",
            message="Cloned from analysis #42.",
        )
        assert resp.analysis_id == 99
        assert resp.status == "queued"


class TestProjectStatusCounts:
    """Test the enhanced ProjectResponse with status_counts."""

    def test_status_counts_model(self):
        from app.routers.projects import AnalysisCountsByStatus
        counts = AnalysisCountsByStatus()
        assert counts.queued == 0
        assert counts.running == 0
        assert counts.complete == 0
        assert counts.failed == 0
        assert counts.cancelled == 0

    def test_status_counts_with_values(self):
        from app.routers.projects import AnalysisCountsByStatus
        counts = AnalysisCountsByStatus(complete=3, failed=1)
        assert counts.complete == 3
        assert counts.failed == 1
        assert counts.queued == 0

    def test_project_response_includes_status_counts(self):
        from app.routers.projects import ProjectResponse, AnalysisCountsByStatus
        resp = ProjectResponse(
            id=1,
            user_id=1,
            name="Test",
            cancer_type="Melanoma",
            stage="III",
            reference_genome="GRCh38",
            created_at=datetime.now(timezone.utc),
            analysis_count=5,
            status_counts=AnalysisCountsByStatus(complete=3, running=2),
        )
        assert resp.status_counts is not None
        assert resp.status_counts.complete == 3
        assert resp.status_counts.running == 2

    def test_project_response_status_counts_optional(self):
        from app.routers.projects import ProjectResponse
        resp = ProjectResponse(
            id=1,
            user_id=1,
            name="Test",
            cancer_type="Melanoma",
            stage=None,
            reference_genome="GRCh38",
            created_at=datetime.now(timezone.utc),
            analysis_count=0,
        )
        assert resp.status_counts is None


class TestRouteRegistration:
    """Verify new routes are registered on the analyses router."""

    def test_list_analyses_route_exists(self):
        from app.routers.analyses import router
        paths = [r.path for r in router.routes]
        assert "/" in paths  # GET / -> list_analyses

    def test_clone_route_exists(self):
        from app.routers.analyses import router
        paths = [r.path for r in router.routes]
        assert "/{analysis_id}/clone" in paths

    def test_dashboard_stats_route_exists(self):
        from app.routers.analyses import router
        paths = [r.path for r in router.routes]
        assert "/stats/dashboard" in paths

    def test_stats_before_parameterized(self):
        """stats/dashboard must be registered before /{analysis_id}
        to avoid FastAPI matching 'stats' as an analysis_id."""
        from app.routers.analyses import router
        paths = [r.path for r in router.routes]
        stats_idx = paths.index("/stats/dashboard")
        param_idx = paths.index("/{analysis_id}")
        assert stats_idx < param_idx, (
            f"/stats/dashboard at index {stats_idx} must come before "
            f"/{{analysis_id}} at index {param_idx}"
        )
