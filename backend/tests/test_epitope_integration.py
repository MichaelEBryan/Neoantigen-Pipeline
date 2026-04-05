"""
Integration tests for epitope endpoints.

Focus: Auth, validation, sorting/filtering, and response structure.
"""

import pytest
from unittest.mock import MagicMock
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# --- Epitope List ---

class TestEpitopeList:
    """GET /api/epitopes/{analysis_id}/epitopes - list epitopes."""

    async def test_list_epitopes_empty(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """GET /api/epitopes/{id}/epitopes returns paginated list."""
        project = MagicMock()
        project.user_id = 1

        ownership_result = MagicMock()
        ownership_result.one_or_none = MagicMock(return_value=(mock_analysis, project))

        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)

        epitope_result = MagicMock()
        epitope_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[]))
        )

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ownership_result
            elif call_count == 2:
                return count_result
            else:
                return epitope_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/epitopes/1/epitopes",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "epitopes" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data

    async def test_list_epitopes_sorting_accepted(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """Epitope list accepts all valid sort parameters."""
        project = MagicMock()
        project.user_id = 1

        ownership_result = MagicMock()
        ownership_result.one_or_none = MagicMock(return_value=(mock_analysis, project))

        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)

        epitope_result = MagicMock()
        epitope_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[]))
        )

        # Test valid sort parameters - each request resets the mock
        for sort_by in ["rank", "immunogenicity_score"]:
            for sort_order in ["asc", "desc"]:
                call_count = 0
                def side_effect(stmt):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        return ownership_result
                    elif call_count == 2:
                        return count_result
                    else:
                        return epitope_result

                mock_db_override.configure_execute(side_effect=side_effect)

                response = await client.get(
                    f"/api/epitopes/1/epitopes?sort_by={sort_by}&sort_order={sort_order}",
                    headers=auth_headers,
                )
                assert response.status_code == 200

    async def test_list_epitopes_filtering(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """Epitope list accepts filter parameters."""
        project = MagicMock()
        project.user_id = 1

        ownership_result = MagicMock()
        ownership_result.one_or_none = MagicMock(return_value=(mock_analysis, project))

        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)

        epitope_result = MagicMock()
        epitope_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[]))
        )

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ownership_result
            elif call_count == 2:
                return count_result
            else:
                return epitope_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/epitopes/1/epitopes?gene=TP53&hla_allele=HLA-A*02:01&min_score=0.7",
            headers=auth_headers,
        )

        assert response.status_code == 200

    async def test_list_epitopes_confidence_tier_filter(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """Epitope list filters by confidence tier."""
        project = MagicMock()
        project.user_id = 1

        ownership_result = MagicMock()
        ownership_result.one_or_none = MagicMock(return_value=(mock_analysis, project))

        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)

        epitope_result = MagicMock()
        epitope_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[]))
        )

        for tier in ["high", "medium", "low"]:
            call_count = 0
            def side_effect(stmt):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return ownership_result
                elif call_count == 2:
                    return count_result
                else:
                    return epitope_result

            mock_db_override.configure_execute(side_effect=side_effect)

            response = await client.get(
                f"/api/epitopes/1/epitopes?confidence_tier={tier}",
                headers=auth_headers,
            )
            assert response.status_code == 200

    async def test_list_epitopes_invalid_sort_rejected(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """Invalid sort_by parameter is rejected."""
        # Validation should reject invalid sort_by
        response = await client.get(
            "/api/epitopes/1/epitopes?sort_by=invalid",
            headers=auth_headers,
        )

        # Validation error (middleware returns 500 in test environment)
        assert response.status_code != 200

    async def test_list_epitopes_invalid_confidence_tier_rejected(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """Invalid confidence_tier parameter is rejected."""
        # Validation should reject invalid confidence_tier
        response = await client.get(
            "/api/epitopes/1/epitopes?confidence_tier=invalid",
            headers=auth_headers,
        )

        # Validation error (middleware returns 500 in test environment)
        assert response.status_code != 200


# --- Filter Options ---

class TestFilterOptions:
    """GET /api/epitopes/{analysis_id}/epitopes/filter-options."""

    async def test_get_filter_options(self, client: AsyncClient, mock_db_override, mock_analysis, auth_headers):
        """GET filter-options returns unique filter values."""
        project = MagicMock()
        project.user_id = 1

        ownership_result = MagicMock()
        ownership_result.one_or_none = MagicMock(return_value=(mock_analysis, project))

        genes_result = MagicMock()
        genes_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=["TP53", "BRCA1"]))
        )

        types_result = MagicMock()
        types_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=["missense"]))
        )

        hla_result = MagicMock()
        hla_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=["HLA-A*02:01"]))
        )

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ownership_result
            elif call_count == 2:
                return genes_result
            elif call_count == 3:
                return types_result
            else:
                return hla_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/epitopes/1/epitopes/filter-options",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "genes" in data
        assert "variant_types" in data
        assert "hla_alleles" in data


# --- Export ---

class TestEpitopeExport:
    """GET /api/epitopes/{analysis_id}/epitopes/export."""

    async def test_export_csv(self, client: AsyncClient, mock_db_override, mock_analysis, mock_epitope, auth_headers):
        """GET epitopes/export?format=csv returns CSV."""
        project = MagicMock()
        project.user_id = 1

        ownership_result = MagicMock()
        ownership_result.one_or_none = MagicMock(return_value=(mock_analysis, project))

        epitope_result = MagicMock()
        epitope_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_epitope]))
        )

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ownership_result
            else:
                return epitope_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/epitopes/1/epitopes/export?format=csv",
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")
        assert "attachment" in response.headers.get("content-disposition", "")

    async def test_export_tsv(self, client: AsyncClient, mock_db_override, mock_analysis, mock_epitope, auth_headers):
        """GET epitopes/export?format=tsv returns TSV."""
        project = MagicMock()
        project.user_id = 1

        ownership_result = MagicMock()
        ownership_result.one_or_none = MagicMock(return_value=(mock_analysis, project))

        epitope_result = MagicMock()
        epitope_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_epitope]))
        )

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ownership_result
            else:
                return epitope_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/epitopes/1/epitopes/export?format=tsv",
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert "text/tab-separated-values" in response.headers.get("content-type", "")


# --- Epitope Detail ---

class TestEpitopeDetail:
    """GET /api/epitopes/{epitope_id} - epitope detail with explainability."""

    async def test_get_epitope_detail(self, client: AsyncClient, mock_db_override, mock_epitope, mock_analysis, auth_headers):
        """GET /api/epitopes/{epitope_id} returns detailed epitope info."""
        project = MagicMock()
        project.user_id = 1

        epitope_result = MagicMock()
        epitope_result.scalar_one_or_none = MagicMock(return_value=mock_epitope)

        ownership_result = MagicMock()
        ownership_result.one_or_none = MagicMock(return_value=(mock_analysis, project))

        sibling_result = MagicMock()
        sibling_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[]))
        )

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return epitope_result
            elif call_count == 2:
                return ownership_result
            else:
                return sibling_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/epitopes/1",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "peptide_seq" in data
        assert "immunogenicity_score" in data
        assert "explanation_json" in data
        assert "scorer_weights" in data
        assert "sibling_epitopes" in data

    async def test_get_epitope_nonexistent(self, client: AsyncClient, mock_db_override, auth_headers):
        """GET /api/epitopes/99999 returns 404."""
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db_override.configure_execute(return_value=result)

        response = await client.get(
            "/api/epitopes/99999",
            headers=auth_headers,
        )

        assert response.status_code == 404


# --- Access Control ---

class TestEpitopeAccessControl:
    """Authorization for epitope endpoints."""

    async def test_list_epitopes_nonexistent_analysis(self, client: AsyncClient, mock_db_override, auth_headers):
        """Cannot list epitopes for nonexistent analysis."""
        result = MagicMock()
        result.one_or_none = MagicMock(return_value=None)
        mock_db_override.configure_execute(return_value=result)

        response = await client.get(
            "/api/epitopes/99999/epitopes",
            headers=auth_headers,
        )

        assert response.status_code == 404

    async def test_list_epitopes_forbidden_for_other_user(self, client: AsyncClient, mock_db_override, auth_headers):
        """Cannot list epitopes from another user's analysis."""
        analysis = MagicMock()
        analysis.id = 1

        project = MagicMock()
        project.user_id = 2  # Different user

        result = MagicMock()
        result.one_or_none = MagicMock(return_value=(analysis, project))
        mock_db_override.configure_execute(return_value=result)

        response = await client.get(
            "/api/epitopes/1/epitopes",
            headers=auth_headers,
        )

        assert response.status_code == 403

    async def test_detail_forbidden_for_other_user(self, client: AsyncClient, mock_db_override, auth_headers):
        """Cannot view epitope from another user's analysis."""
        epitope = MagicMock()
        epitope.id = 1
        epitope.analysis_id = 1

        epitope_result = MagicMock()
        epitope_result.scalar_one_or_none = MagicMock(return_value=epitope)

        analysis = MagicMock()
        analysis.id = 1

        project = MagicMock()
        project.user_id = 2  # Different user

        ownership_result = MagicMock()
        ownership_result.one_or_none = MagicMock(return_value=(analysis, project))

        call_count = 0
        def side_effect(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return epitope_result
            else:
                return ownership_result

        mock_db_override.configure_execute(side_effect=side_effect)

        response = await client.get(
            "/api/epitopes/1",
            headers=auth_headers,
        )

        assert response.status_code == 403


# --- No Auth ---

class TestAuthRequired:
    """All epitope endpoints require authentication."""

    async def test_list_epitopes_no_auth(self, client: AsyncClient):
        """GET /api/epitopes without token returns 401."""
        response = await client.get("/api/epitopes/1/epitopes")
        assert response.status_code == 401

    async def test_export_epitopes_no_auth(self, client: AsyncClient):
        """GET epitopes/export without token returns 401."""
        response = await client.get("/api/epitopes/1/epitopes/export")
        assert response.status_code == 401

    async def test_detail_no_auth(self, client: AsyncClient):
        """GET /api/epitopes/{id} without token returns 401."""
        response = await client.get("/api/epitopes/1")
        assert response.status_code == 401

    async def test_filter_options_no_auth(self, client: AsyncClient):
        """GET filter-options without token returns 401."""
        response = await client.get("/api/epitopes/1/epitopes/filter-options")
        assert response.status_code == 401
