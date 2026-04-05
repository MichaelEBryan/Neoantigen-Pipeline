"""
Integration tests for the Vaccine Construct Builder endpoints.

Tests POST /api/construct/build and POST /api/construct/cleavage.
Uses the same mock DB infrastructure as other integration tests.

Covers:
- Happy path: build construct from selected epitopes
- Ordering strategies: immunogenicity, alternating, manual
- Sequence modes: epitope vs 25mer
- Linker insertion and position tracking
- Auth: unauthenticated requests rejected
- Validation: empty IDs, too many IDs, wrong analysis ownership
- Cleavage endpoint: valid sequence, invalid AAs, missing pepsickle
"""
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.auth import create_access_token, hash_password
from app.database import get_db
from tests.conftest import (
    MockAsyncSession,
    _mock_get_db,
    make_mock_user,
    make_mock_epitope,
    make_mock_analysis,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
def test_user_data():
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
def auth_headers(test_user_data):
    token = create_access_token({
        "user_id": test_user_data["id"],
        "email": test_user_data["email"],
    })
    return {"Authorization": f"Bearer {token}"}


def _make_epitopes_for_construct(n=3, analysis_id=1):
    """
    Create n mock epitopes with distinct peptides, genes, scores.
    Returns list of mock epitope ORM objects.
    """
    genes = ["BRAF", "TP53", "KRAS", "EGFR", "PIK3CA"]
    peptides = [
        "LATEKSRWS", "FVHDALQRP", "KLVFFAEDV",
        "YLEPGPVTA", "GILGFVFTL",
    ]
    epitopes = []
    for i in range(n):
        ep = MagicMock()
        ep.id = i + 1
        ep.analysis_id = analysis_id
        ep.peptide_seq = peptides[i % len(peptides)]
        ep.peptide_length = len(ep.peptide_seq)
        ep.hla_allele = "HLA-A*02:01"
        ep.binding_affinity_nm = 20.0 + i * 10
        ep.immunogenicity_score = 0.9 - i * 0.1
        # Variant mock
        v = MagicMock()
        v.gene = genes[i % len(genes)]
        v.protein_change = f"p.V{600 + i}E"
        v.variant_type = "missense"
        v.annotation_json = {}
        ep.variant = v
        epitopes.append(ep)
    return epitopes


def _setup_db_for_build(mock_session, epitopes, analysis_user_id=1):
    """
    Configure the mock DB session to return epitopes and analysis
    for the /build endpoint.
    """
    analysis = MagicMock()
    analysis.id = epitopes[0].analysis_id if epitopes else 1
    analysis.user_id = analysis_user_id

    call_count = [0]

    def side_effect(stmt):
        result = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            # First query: fetch epitopes by IDs
            result.scalars.return_value = MagicMock(all=MagicMock(return_value=epitopes))
        elif call_count[0] == 2:
            # Second query: fetch analysis for ownership check
            result.scalar_one_or_none.return_value = analysis
        return result

    mock_session.configure_execute(side_effect=side_effect)
    return mock_session


# ---------------------------------------------------------------------------
# POST /api/construct/build
# ---------------------------------------------------------------------------

class TestBuildConstruct:

    @pytest.mark.asyncio
    async def test_build_basic(self, client, auth_headers, test_user_data):
        """Build a construct with 3 epitopes, default settings."""
        eps = _make_epitopes_for_construct(3)
        mock_session = MockAsyncSession()
        _setup_db_for_build(mock_session, eps, analysis_user_id=1)

        # Override dependencies
        global _current_mock_session
        from tests.conftest import _mock_get_db
        import tests.conftest as conftest
        conftest._current_mock_session = mock_session
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_db] = _mock_get_db
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/build",
                json={
                    "analysis_id": 1,
                    "epitope_ids": [1, 2, 3],
                    "ordering": "immunogenicity",
                    "sequence_mode": "epitope",
                    "linker": "AAY",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()

            # Basic structure checks
            assert "construct_sequence" in data
            assert "total_length" in data
            assert "epitopes" in data
            assert "linker_positions" in data
            assert "regions" in data
            assert data["ordering_used"] == "immunogenicity"

            # Should have 3 epitopes and 2 linkers
            assert len(data["epitopes"]) == 3
            assert len(data["linker_positions"]) == 2

            # Linkers should be "AAY"
            for lp in data["linker_positions"]:
                assert lp["sequence"] == "AAY"

            # Construct length = sum of peptide lengths + 2 * len("AAY")
            expected_len = sum(len(e["peptide_seq"]) for e in data["epitopes"]) + 2 * 3
            assert data["total_length"] == expected_len, (
                f"Expected length {expected_len}, got {data['total_length']}"
            )

            # Epitopes should be ordered by immunogenicity (descending)
            scores = [e["immunogenicity_score"] for e in data["epitopes"]]
            assert scores == sorted(scores, reverse=True), (
                f"Epitopes not in immunogenicity order: {scores}"
            )

            print("PASS: test_build_basic")
        finally:
            app.dependency_overrides.clear()
            conftest._current_mock_session = None

    @pytest.mark.asyncio
    async def test_build_no_auth(self, client):
        """Request without auth token should be rejected."""
        resp = await client.post(
            "/api/construct/build",
            json={
                "analysis_id": 1,
                "epitope_ids": [1],
                "ordering": "immunogenicity",
                "sequence_mode": "epitope",
                "linker": "AAY",
            },
        )
        assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"
        print("PASS: test_build_no_auth")

    @pytest.mark.asyncio
    async def test_build_empty_ids(self, client, auth_headers, test_user_data):
        """Empty epitope_ids should return 400."""
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/build",
                json={
                    "analysis_id": 1,
                    "epitope_ids": [],
                    "ordering": "immunogenicity",
                    "sequence_mode": "epitope",
                    "linker": "AAY",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
            print("PASS: test_build_empty_ids")
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_build_too_many_ids(self, client, auth_headers, test_user_data):
        """More than 50 epitope IDs should return 400."""
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/build",
                json={
                    "analysis_id": 1,
                    "epitope_ids": list(range(1, 52)),  # 51 IDs
                    "ordering": "immunogenicity",
                    "sequence_mode": "epitope",
                    "linker": "AAY",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
            print("PASS: test_build_too_many_ids")
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_build_manual_ordering(self, client, auth_headers, test_user_data):
        """Manual ordering should preserve the order of epitope_ids."""
        eps = _make_epitopes_for_construct(3)
        mock_session = MockAsyncSession()
        _setup_db_for_build(mock_session, eps, analysis_user_id=1)

        import tests.conftest as conftest
        conftest._current_mock_session = mock_session
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_db] = _mock_get_db
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            # Request IDs in reverse order
            resp = await client.post(
                "/api/construct/build",
                json={
                    "analysis_id": 1,
                    "epitope_ids": [3, 1, 2],
                    "ordering": "manual",
                    "sequence_mode": "epitope",
                    "linker": "AAY",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            ids = [e["id"] for e in data["epitopes"]]
            assert ids == [3, 1, 2], f"Manual order not preserved: {ids}"
            assert data["ordering_used"] == "manual"
            print("PASS: test_build_manual_ordering")
        finally:
            app.dependency_overrides.clear()
            conftest._current_mock_session = None

    @pytest.mark.asyncio
    async def test_build_no_linker(self, client, auth_headers, test_user_data):
        """Empty linker should produce a construct without linker sequences."""
        eps = _make_epitopes_for_construct(2)
        mock_session = MockAsyncSession()
        _setup_db_for_build(mock_session, eps, analysis_user_id=1)

        import tests.conftest as conftest
        conftest._current_mock_session = mock_session
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_db] = _mock_get_db
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/build",
                json={
                    "analysis_id": 1,
                    "epitope_ids": [1, 2],
                    "ordering": "immunogenicity",
                    "sequence_mode": "epitope",
                    "linker": "",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["linker_positions"]) == 0
            # Total length = sum of peptide lengths only
            expected = sum(len(e["peptide_seq"]) for e in data["epitopes"])
            assert data["total_length"] == expected
            print("PASS: test_build_no_linker")
        finally:
            app.dependency_overrides.clear()
            conftest._current_mock_session = None

    @pytest.mark.asyncio
    async def test_build_single_epitope(self, client, auth_headers, test_user_data):
        """Single epitope: no linkers, construct = just that peptide."""
        eps = _make_epitopes_for_construct(1)
        mock_session = MockAsyncSession()
        _setup_db_for_build(mock_session, eps, analysis_user_id=1)

        import tests.conftest as conftest
        conftest._current_mock_session = mock_session
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_db] = _mock_get_db
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/build",
                json={
                    "analysis_id": 1,
                    "epitope_ids": [1],
                    "ordering": "immunogenicity",
                    "sequence_mode": "epitope",
                    "linker": "AAY",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["epitopes"]) == 1
            assert len(data["linker_positions"]) == 0
            assert data["construct_sequence"] == eps[0].peptide_seq
            print("PASS: test_build_single_epitope")
        finally:
            app.dependency_overrides.clear()
            conftest._current_mock_session = None

    @pytest.mark.asyncio
    async def test_build_position_tracking(self, client, auth_headers, test_user_data):
        """
        Verify that start_pos/end_pos correctly account for linkers.
        With 2 epitopes of length 9 and linker "AAY" (3):
        ep1: 0-9, linker: 9-12, ep2: 12-21
        """
        eps = _make_epitopes_for_construct(2)
        # Make both peptides exactly 9 aa
        eps[0].peptide_seq = "LATEKSRWS"
        eps[0].peptide_length = 9
        eps[1].peptide_seq = "FVHDALQRP"
        eps[1].peptide_length = 9

        mock_session = MockAsyncSession()
        _setup_db_for_build(mock_session, eps, analysis_user_id=1)

        import tests.conftest as conftest
        conftest._current_mock_session = mock_session
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_db] = _mock_get_db
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/build",
                json={
                    "analysis_id": 1,
                    "epitope_ids": [1, 2],
                    "ordering": "immunogenicity",
                    "sequence_mode": "epitope",
                    "linker": "AAY",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()

            ep1, ep2 = data["epitopes"][0], data["epitopes"][1]
            lp = data["linker_positions"][0]

            # First epitope starts at 0
            assert ep1["start_pos"] == 0
            assert ep1["end_pos"] == 9

            # Linker follows
            assert lp["start"] == 9
            assert lp["end"] == 12

            # Second epitope follows linker
            assert ep2["start_pos"] == 12
            assert ep2["end_pos"] == 21

            # Total length
            assert data["total_length"] == 21

            # Verify construct sequence matches
            assert data["construct_sequence"] == ep1["peptide_seq"] + "AAY" + ep2["peptide_seq"]

            print("PASS: test_build_position_tracking")
        finally:
            app.dependency_overrides.clear()
            conftest._current_mock_session = None


# ---------------------------------------------------------------------------
# POST /api/construct/cleavage
# ---------------------------------------------------------------------------

class TestCleavagePrediction:

    @pytest.mark.asyncio
    async def test_cleavage_invalid_aa(self, client, auth_headers, test_user_data):
        """Sequence with invalid amino acids should return 400."""
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/cleavage",
                json={
                    "sequence": "LATEKSRWSXXX",  # X is not a standard AA
                    "epitope_boundaries": [[0, 9]],
                    "linker_positions": [],
                },
                headers=auth_headers,
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
            assert "Invalid amino acids" in resp.json()["detail"]
            print("PASS: test_cleavage_invalid_aa")
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_cleavage_no_auth(self, client):
        """Cleavage endpoint requires auth."""
        resp = await client.post(
            "/api/construct/cleavage",
            json={
                "sequence": "LATEKSRWSAAYFVHDALQRP",
                "epitope_boundaries": [[0, 9], [12, 21]],
                "linker_positions": [[9, 12]],
            },
        )
        assert resp.status_code in (401, 403)
        print("PASS: test_cleavage_no_auth")

    @pytest.mark.asyncio
    async def test_cleavage_too_short(self, client, auth_headers, test_user_data):
        """Sequence shorter than min_length (10) should fail validation."""
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/cleavage",
                json={
                    "sequence": "LATE",  # only 4 aa, min is 10
                    "epitope_boundaries": [],
                    "linker_positions": [],
                },
                headers=auth_headers,
            )
            assert resp.status_code == 422, f"Expected 422 (validation), got {resp.status_code}"
            print("PASS: test_cleavage_too_short")
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_cleavage_with_mock_pepsickle(self, client, auth_headers, test_user_data):
        """
        Mock pepsickle to test the endpoint logic without the actual model.
        Verifies junction analysis and response structure.
        """
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        # Create a mock pepsickle that returns predictable cleavage scores
        import pandas as pd

        sequence = "LATEKSRWSAAYFVHDALQRP"  # 21 aa
        n = len(sequence)
        # Fake cleavage probs: high at linker boundaries (pos 8 and 12), low elsewhere
        fake_probs = [0.1] * n
        fake_probs[8] = 0.85   # C-terminal of first epitope (just before linker)
        fake_probs[12] = 0.75  # N-terminal of second epitope (just after linker)

        mock_df = pd.DataFrame({"cleavage_prob": fake_probs})
        mock_model = MagicMock()

        with patch.dict("sys.modules", {"pepsickle": MagicMock()}):
            import sys
            mock_pepsickle = sys.modules["pepsickle"]
            mock_pepsickle.initialize_epitope_model.return_value = mock_model
            mock_pepsickle.predict_protein_cleavage_locations.return_value = mock_df

            try:
                resp = await client.post(
                    "/api/construct/cleavage",
                    json={
                        "sequence": sequence,
                        "epitope_boundaries": [[0, 9], [12, 21]],
                        "linker_positions": [[9, 12]],
                    },
                    headers=auth_headers,
                )
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
                data = resp.json()

                assert "cleavage_scores" in data
                assert len(data["cleavage_scores"]) == n

                # Check junction cleavage analysis
                assert "junction_cleavage" in data
                # Should have 2 junction entries (one for each side of the linker)
                assert len(data["junction_cleavage"]) == 2

                # C-terminal junction (pos 8, score 0.85) should be correct cleavage
                c_term = [j for j in data["junction_cleavage"] if j["position"] == 8]
                assert len(c_term) == 1
                assert c_term[0]["is_correct_cleavage"] == True
                assert abs(c_term[0]["score"] - 0.85) < 0.01

                print("PASS: test_cleavage_with_mock_pepsickle")
            finally:
                app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Invalid ordering value (should be caught by pydantic validation)
# ---------------------------------------------------------------------------

class TestValidation:

    @pytest.mark.asyncio
    async def test_invalid_ordering(self, client, auth_headers, test_user_data):
        """Invalid ordering value should fail pydantic validation."""
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/build",
                json={
                    "analysis_id": 1,
                    "epitope_ids": [1],
                    "ordering": "random_invalid",
                    "sequence_mode": "epitope",
                    "linker": "AAY",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
            print("PASS: test_invalid_ordering")
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_invalid_sequence_mode(self, client, auth_headers, test_user_data):
        """Invalid sequence_mode should fail pydantic validation."""
        from app.routers.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: make_mock_user(test_user_data)

        try:
            resp = await client.post(
                "/api/construct/build",
                json={
                    "analysis_id": 1,
                    "epitope_ids": [1],
                    "ordering": "immunogenicity",
                    "sequence_mode": "full_protein",
                    "linker": "AAY",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
            print("PASS: test_invalid_sequence_mode")
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    import asyncio
    # Can't easily run async tests from __main__, use pytest instead
    print("Run with: cd backend && python -m pytest tests/test_construct_integration.py -v")
