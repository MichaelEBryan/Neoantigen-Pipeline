"""
Tests for Task 14: Privacy, Citation, and Legal.

Tests the export and delete endpoint models and route registration.
No DB required -- pure unit tests.
"""
import pytest
from datetime import datetime, timezone


class TestDeleteAccountResponse:
    def test_shape(self):
        from app.routers.auth import DeleteAccountResponse
        resp = DeleteAccountResponse(message="Deleted.")
        assert resp.message == "Deleted."


class TestRouteRegistration:
    """Verify new routes are registered on the auth router."""

    def test_export_route_exists(self):
        from app.routers.auth import router
        paths = [r.path for r in router.routes]
        assert "/export-my-data" in paths

    def test_delete_route_exists(self):
        from app.routers.auth import router
        paths = [r.path for r in router.routes]
        assert "/delete-my-account" in paths

    def test_export_is_get(self):
        from app.routers.auth import router
        for route in router.routes:
            if getattr(route, "path", None) == "/export-my-data":
                assert "GET" in route.methods
                break
        else:
            pytest.fail("/export-my-data route not found")

    def test_delete_is_delete_method(self):
        from app.routers.auth import router
        for route in router.routes:
            if getattr(route, "path", None) == "/delete-my-account":
                assert "DELETE" in route.methods
                break
        else:
            pytest.fail("/delete-my-account route not found")


class TestImports:
    """Make sure the new imports didn't break anything."""

    def test_auth_router_loads(self):
        from app.routers.auth import router
        assert router is not None

    def test_all_models_imported(self):
        """The auth module now imports all models for cascade delete."""
        from app.routers import auth
        assert hasattr(auth, "Project")
        assert hasattr(auth, "Analysis")
        assert hasattr(auth, "Epitope")
        assert hasattr(auth, "Variant")
        assert hasattr(auth, "JobLog")
        assert hasattr(auth, "HLAType")
        assert hasattr(auth, "AnalysisInput")
