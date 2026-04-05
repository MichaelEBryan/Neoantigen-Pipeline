"""
Compute dispatch orchestrator.

Bridges the ComputeBackend protocol with the existing Celery task layer.
Handles the full lifecycle of a remote variant-calling job:

  1. Build SubmitRequest from Analysis DB record
  2. Submit to the configured backend (GCP Batch or Isambard)
  3. Store the backend job ID in the Analysis record
  4. Poll periodically until the job finishes
  5. Download VCF and hand off to the local scoring pipeline
  6. Clean up remote resources

This module is called from the Celery task in celery_app.py.
It never touches the event loop directly -- all async calls are
run through asyncio.run() from the sync Celery worker context.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.compute.backend import (
    ComputeBackend,
    ComputeError,
    JobState,
    SubmitRequest,
    get_compute_backend,
)
from app.config import settings
from app.models import Analysis, AnalysisInput, HLAType, JobLog

logger = logging.getLogger(__name__)

# How often to poll the backend (seconds). Starts at 15s, backs off to 60s.
POLL_INTERVAL_MIN = 15
POLL_INTERVAL_MAX = 60
POLL_BACKOFF_FACTOR = 1.5

# Maximum time to wait for a job (12 hours)
MAX_POLL_DURATION = 43200


async def _log_step(
    db: AsyncSession,
    analysis_id: int,
    step: str,
    status: str,
    message: str,
) -> None:
    """Write a job log entry. Mirrors orchestrator._log_step but doesn't
    publish to Redis (the dispatch steps are coarser-grained)."""
    from app.pipeline.progress import publish_progress

    log = JobLog(
        analysis_id=analysis_id,
        step=step,
        status=status,
        message=message,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(log)
    await db.flush()

    # Publish to WebSocket clients
    # Map dispatch steps to approximate progress percentages
    step_progress = {
        "upload_received": 0.02,
        "remote_upload": 0.08,
        "remote_submit": 0.12,
        "remote_running": 0.50,
        "remote_download": 0.85,
        "vcf_parsing": 0.88,
    }
    pct = step_progress.get(step, 0.0)
    await publish_progress(analysis_id, step, status, message, pct)


async def build_submit_request(
    db: AsyncSession,
    analysis: Analysis,
) -> SubmitRequest:
    """Build a SubmitRequest from an Analysis record.

    Reads input files and HLA alleles from the database.
    """
    # Get input files
    stmt = select(AnalysisInput).where(
        AnalysisInput.analysis_id == analysis.id,
    )
    result = await db.execute(stmt)
    inputs = result.scalars().all()

    if not inputs:
        raise ValueError(f"No input files for analysis {analysis.id}")

    # Determine input type from file extensions
    input_files = []
    input_type = "vcf"  # default
    for inp in inputs:
        input_files.append(inp.file_path)
        if inp.file_type in ("fastq", "bam"):
            input_type = inp.file_type

    # Get HLA alleles
    hla_stmt = select(HLAType).where(HLAType.analysis_id == analysis.id)
    hla_result = await db.execute(hla_stmt)
    hla_alleles = [h.allele for h in hla_result.scalars().all()]

    if not hla_alleles:
        raise ValueError(f"No HLA alleles for analysis {analysis.id}")

    # Reference genome from analysis metadata or default
    reference = "GRCh38"
    if analysis.metadata_json and isinstance(analysis.metadata_json, dict):
        reference = analysis.metadata_json.get("reference_genome", "GRCh38")

    # Tumor-normal pairing
    paired = False
    if analysis.metadata_json and isinstance(analysis.metadata_json, dict):
        paired = analysis.metadata_json.get("tumor_normal_paired", False)

    return SubmitRequest(
        analysis_id=analysis.id,
        input_files=input_files,
        input_type=input_type,
        hla_alleles=hla_alleles,
        reference_genome=reference,
        tumor_normal_paired=paired,
    )


async def dispatch_and_wait(
    analysis_id: int,
    backend: ComputeBackend | None = None,
) -> str:
    """Submit a job, poll until completion, download VCF, return local VCF path.

    This is the main entry point called from the Celery task. It:
      1. Loads the analysis from DB
      2. Builds a SubmitRequest
      3. Submits to the compute backend
      4. Polls until terminal state
      5. Downloads VCF on success
      6. Cleans up remote resources
      7. Returns the local VCF path (ready for the scoring pipeline)

    Raises ComputeError on any failure.
    """
    if backend is None:
        backend = get_compute_backend()

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as db:
            # Load analysis
            stmt = select(Analysis).where(Analysis.id == analysis_id)
            result = await db.execute(stmt)
            analysis = result.scalar_one_or_none()

            if not analysis:
                raise ValueError(f"Analysis {analysis_id} not found")

            # Mark as running
            analysis.status = "running"
            await db.flush()

            await _log_step(
                db, analysis_id, "upload_received", "complete",
                "Analysis record loaded"
            )

            # Build request
            request = await build_submit_request(db, analysis)

            # Submit
            await _log_step(
                db, analysis_id, "remote_upload", "running",
                f"Uploading {len(request.input_files)} files to {backend.name}..."
            )

            job_id = await backend.submit(request)

            # Store backend job ID in analysis metadata
            meta = analysis.metadata_json or {}
            meta["compute_job_id"] = job_id
            meta["compute_backend"] = backend.name
            analysis.metadata_json = meta
            await db.flush()

            await _log_step(
                db, analysis_id, "remote_submit", "complete",
                f"Job submitted to {backend.name}: {job_id}"
            )

            await db.commit()

        # -- Poll loop (outside the DB session to avoid long-held connections) --
        poll_interval = POLL_INTERVAL_MIN
        start_time = time.monotonic()

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > MAX_POLL_DURATION:
                # Timeout -- cancel the job
                await backend.cancel(job_id)
                async with session_factory() as db:
                    await _log_step(
                        db, analysis_id, "remote_running", "failed",
                        f"Job timed out after {MAX_POLL_DURATION}s"
                    )
                    await db.execute(
                        update(Analysis)
                        .where(Analysis.id == analysis_id)
                        .values(status="failed")
                    )
                    await db.commit()
                raise ComputeError(
                    f"Job {job_id} timed out after {MAX_POLL_DURATION}s",
                    backend=backend.name,
                    job_id=job_id,
                )

            status = await backend.poll(job_id)

            if status.state == JobState.RUNNING:
                async with session_factory() as db:
                    await _log_step(
                        db, analysis_id, "remote_running", "running",
                        status.message or f"Running on {backend.name}..."
                    )
                    await db.commit()

            if status.state.is_terminal:
                break

            await asyncio.sleep(poll_interval)
            poll_interval = min(poll_interval * POLL_BACKOFF_FACTOR, POLL_INTERVAL_MAX)

        # -- Handle terminal state --
        if status.state == JobState.FAILED:
            async with session_factory() as db:
                await _log_step(
                    db, analysis_id, "remote_running", "failed",
                    f"Job failed: {status.message}"
                )
                await db.execute(
                    update(Analysis)
                    .where(Analysis.id == analysis_id)
                    .values(status="failed")
                )
                await db.commit()
            await backend.cleanup(job_id)
            raise ComputeError(
                f"Remote job failed: {status.message}",
                backend=backend.name,
                job_id=job_id,
            )

        if status.state == JobState.CANCELLED:
            async with session_factory() as db:
                await _log_step(
                    db, analysis_id, "remote_running", "failed",
                    "Job was cancelled"
                )
                await db.execute(
                    update(Analysis)
                    .where(Analysis.id == analysis_id)
                    .values(status="cancelled")
                )
                await db.commit()
            await backend.cleanup(job_id)
            raise ComputeError(
                "Job was cancelled",
                backend=backend.name,
                job_id=job_id,
            )

        # -- Success: download VCF --
        dest_dir = os.path.join(settings.upload_dir, "results", str(analysis_id))
        os.makedirs(dest_dir, exist_ok=True)

        async with session_factory() as db:
            await _log_step(
                db, analysis_id, "remote_download", "running",
                "Downloading VCF from compute backend..."
            )
            await db.commit()

        job_result = await backend.retrieve_results(job_id, dest_dir)

        async with session_factory() as db:
            await _log_step(
                db, analysis_id, "remote_download", "complete",
                f"Downloaded VCF: {Path(job_result.vcf_local_path).name}"
            )

            # Store the VCF path as an AnalysisInput so the scoring pipeline
            # can find it (same pattern as direct VCF upload)
            vcf_input = AnalysisInput(
                analysis_id=analysis_id,
                file_type="vcf",
                file_path=job_result.vcf_local_path,
                file_name=Path(job_result.vcf_local_path).name,
                file_size=os.path.getsize(job_result.vcf_local_path),
            )
            db.add(vcf_input)
            await db.commit()

        # Cleanup remote resources (best-effort)
        await backend.cleanup(job_id)

        return job_result.vcf_local_path

    finally:
        await engine.dispose()
