"""
Tests for Task 5: Job Queue and Status API.

Tests the progress calculation, pipeline step definitions,
and status response models. Does not require Redis or a live DB.
"""
import pytest
from datetime import datetime, timezone


class TestProgressCalculation:
    """Test progress_for_step in the WS module."""

    def test_import(self):
        from app.routers.ws import PIPELINE_STEPS, progress_for_step
        assert len(PIPELINE_STEPS) == 9

    def test_unknown_step_returns_zero(self):
        from app.routers.ws import progress_for_step
        assert progress_for_step("nonexistent", "complete") == 0.0

    def test_first_step_running(self):
        from app.routers.ws import progress_for_step
        pct = progress_for_step("upload_received", "running")
        assert 0.0 < pct < 0.05

    def test_first_step_complete(self):
        from app.routers.ws import progress_for_step
        pct = progress_for_step("upload_received", "complete")
        assert 0.01 < pct < 0.05

    def test_mhc_prediction_is_heaviest(self):
        """MHC prediction has weight 0.40, so its completion should be a big jump."""
        from app.routers.ws import progress_for_step
        before = progress_for_step("peptide_generation", "complete")
        after = progress_for_step("mhc_prediction", "complete")
        jump = after - before
        assert jump > 0.3, f"Expected big jump, got {jump}"

    def test_done_step_is_100_pct(self):
        from app.routers.ws import progress_for_step
        assert progress_for_step("done", "complete") == 1.0

    def test_running_is_less_than_complete(self):
        from app.routers.ws import progress_for_step
        for step in ["vcf_parsing", "mhc_prediction", "scoring"]:
            running = progress_for_step(step, "running")
            complete = progress_for_step(step, "complete")
            assert running < complete, f"{step}: running={running} >= complete={complete}"

    def test_progress_monotonically_increases(self):
        from app.routers.ws import PIPELINE_STEPS, progress_for_step
        prev = 0.0
        for step in PIPELINE_STEPS:
            pct = progress_for_step(step["key"], "complete")
            assert pct >= prev, f"{step['key']}: {pct} < {prev}"
            prev = pct


class TestPipelineStepDefinitions:
    """Test that the step definitions are consistent between modules."""

    def test_ws_and_analyses_have_same_steps(self):
        from app.routers.ws import PIPELINE_STEPS as ws_steps
        from app.routers.analyses import PIPELINE_STEPS as api_steps
        assert ws_steps == api_steps

    def test_weights_sum_to_one(self):
        from app.routers.ws import PIPELINE_STEPS
        total = sum(s["weight"] for s in PIPELINE_STEPS)
        assert abs(total - 1.0) < 0.01, f"Weights sum to {total}"

    def test_all_steps_have_required_fields(self):
        from app.routers.ws import PIPELINE_STEPS
        for step in PIPELINE_STEPS:
            assert "key" in step
            assert "label" in step
            assert "weight" in step
            assert isinstance(step["weight"], (int, float))


class TestStatusResponseModels:
    """Test the Pydantic response models."""

    def test_pipeline_step_status_model(self):
        from app.routers.analyses import PipelineStepStatus
        step = PipelineStepStatus(
            key="vcf_parsing",
            label="VCF Parsing",
            status="running",
            message="Parsing VCF...",
            timestamp=datetime.now(timezone.utc),
        )
        assert step.key == "vcf_parsing"
        assert step.status == "running"

    def test_pipeline_step_status_pending(self):
        from app.routers.analyses import PipelineStepStatus
        step = PipelineStepStatus(
            key="mhc_prediction",
            label="MHC Binding Prediction",
            status="pending",
        )
        assert step.message is None
        assert step.timestamp is None

    def test_analysis_status_response_model(self):
        from app.routers.analyses import AnalysisStatusResponse, PipelineStepStatus, JobProgressItem
        now = datetime.now(timezone.utc)
        resp = AnalysisStatusResponse(
            analysis_id=1,
            status="running",
            progress_pct=0.45,
            pipeline_steps=[
                PipelineStepStatus(key="vcf_parsing", label="VCF Parsing", status="complete"),
            ],
            job_progress=[
                JobProgressItem(step="vcf_parsing", status="complete", message="Done", timestamp=now),
            ],
            variant_count=5,
            epitope_count=0,
            updated_at=now,
        )
        assert resp.progress_pct == 0.45
        assert len(resp.pipeline_steps) == 1

    def test_cancel_response_model(self):
        from app.routers.analyses import CancelResponse
        resp = CancelResponse(analysis_id=1, status="cancelled", message="Cancelled")
        assert resp.status == "cancelled"

    def test_retry_response_model(self):
        from app.routers.analyses import RetryResponse
        resp = RetryResponse(analysis_id=1, status="queued", message="Re-queued")
        assert resp.status == "queued"


class TestOrchestratorProgressMap:
    """Test that the orchestrator's _get_step_progress is consistent."""

    def test_step_progress_map_exists(self):
        from app.pipeline.orchestrator import _get_step_progress
        sp = _get_step_progress()
        assert len(sp) == 9  # all 9 pipeline steps

    def test_step_progress_values_increase(self):
        from app.pipeline.orchestrator import _get_step_progress
        sp = _get_step_progress()
        prev = 0.0
        for key, val in sp.items():
            assert val > prev, f"{key}: {val} <= {prev}"
            prev = val

    def test_step_progress_max_is_one(self):
        from app.pipeline.orchestrator import _get_step_progress
        sp = _get_step_progress()
        assert abs(sp["done"] - 1.0) < 0.01

    def test_orchestrator_matches_ws_cumulative(self):
        """The orchestrator's cumulative weights must match ws.py's."""
        from app.pipeline.orchestrator import _get_step_progress
        from app.routers.ws import _CUMULATIVE
        sp = _get_step_progress()
        for key in _CUMULATIVE:
            assert abs(sp[key] - _CUMULATIVE[key]) < 0.001, (
                f"Mismatch at {key}: orchestrator={sp[key]}, ws={_CUMULATIVE[key]}"
            )
