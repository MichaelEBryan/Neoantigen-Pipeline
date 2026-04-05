import re
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func, case
from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime
from app.database import get_db
from app.models import Analysis, AnalysisInput, Project, JobLog, Variant, Epitope, HLAType, User
from app.auth import get_current_user
from app.pipeline.progress import store_celery_task_id, get_celery_task_id
from app.pipeline.expression_parser import parse_expression_matrix, ExpressionParseError

logger = logging.getLogger(__name__)

router = APIRouter()


# Pipeline step definitions -- shared with frontend for progress display.
# Same order as in ws.py and orchestrator.py.
PIPELINE_STEPS = [
    {"key": "upload_received", "label": "Upload Received", "weight": 0.02},
    {"key": "vcf_parsing", "label": "VCF Parsing", "weight": 0.08},
    {"key": "variant_storage", "label": "Storing Variants", "weight": 0.05},
    {"key": "peptide_generation", "label": "Peptide Generation", "weight": 0.10},
    {"key": "mhc_prediction", "label": "MHC Binding Prediction", "weight": 0.40},
    {"key": "scoring", "label": "Immunogenicity Scoring", "weight": 0.15},
    {"key": "ranking", "label": "Ranking & Selection", "weight": 0.05},
    {"key": "results_storage", "label": "Storing Results", "weight": 0.10},
    {"key": "done", "label": "Complete", "weight": 0.05},
]

_STEP_KEYS = [s["key"] for s in PIPELINE_STEPS]
_CUMULATIVE: dict[str, float] = {}
_running_total = 0.0
for _s in PIPELINE_STEPS:
    _running_total += _s["weight"]
    _CUMULATIVE[_s["key"]] = round(_running_total, 3)


VALID_INPUT_TYPES = {"fastq", "bam", "vcf"}


class AnalysisCreate(BaseModel):
    """Request model for creating a new analysis."""
    project_id: int
    input_type: str  # fastq/bam/vcf
    hla_provided: bool = False

    @field_validator("input_type")
    @classmethod
    def validate_input_type(cls, v: str) -> str:
        if v not in VALID_INPUT_TYPES:
            raise ValueError(
                f"Invalid input_type '{v}'. Must be one of: {', '.join(sorted(VALID_INPUT_TYPES))}"
            )
        return v


class AnalysisResponse(BaseModel):
    """Response model for analysis details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    status: str
    input_type: str
    hla_provided: bool
    isambard_job_id: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    # Optional extras populated by list/detail endpoints
    project_name: Optional[str] = None
    cancer_type: Optional[str] = None
    variant_count: Optional[int] = None
    epitope_count: Optional[int] = None
    # Per-project sequential number (1-based): "Analysis #1 of 3"
    project_analysis_number: Optional[int] = None
    project_analysis_total: Optional[int] = None


class AnalysisListResponse(BaseModel):
    """Paginated list of analyses across all user's projects."""
    analyses: List[AnalysisResponse]
    total: int


class JobProgressItem(BaseModel):
    """Single step in job progress."""
    step: str
    status: str
    message: Optional[str]
    timestamp: datetime


class PipelineStepDef(BaseModel):
    """Definition of a pipeline step for the frontend."""
    key: str
    label: str
    weight: float


class PipelineStepStatus(BaseModel):
    """Current status of a pipeline step."""
    key: str
    label: str
    status: str  # pending/running/complete/failed
    message: Optional[str] = None
    timestamp: Optional[datetime] = None


class AnalysisStatusResponse(BaseModel):
    """Response model for analysis status with job progress."""
    model_config = ConfigDict(from_attributes=True)

    analysis_id: int
    status: str
    progress_pct: float  # 0.0 to 1.0
    pipeline_steps: List[PipelineStepStatus]
    job_progress: List[JobProgressItem]  # raw log entries (backward compat)
    variant_count: int
    epitope_count: int
    updated_at: datetime


async def _get_analysis_with_ownership(
    analysis_id: int,
    current_user: User,
    db: AsyncSession,
) -> Analysis:
    """
    Fetch an analysis and verify the current user owns the parent project.
    Single query with join -- no TOCTOU window.
    Raises 404 if not found, 403 if not the owner.
    """
    stmt = (
        select(Analysis, Project)
        .join(Project, Analysis.project_id == Project.id)
        .where(Analysis.id == analysis_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found"
        )

    analysis, project = row

    if project.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this analysis"
        )

    return analysis


@router.post("/", response_model=AnalysisResponse, status_code=status.HTTP_201_CREATED)
async def create_analysis(
    analysis_data: AnalysisCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisResponse:
    """
    Create a new analysis for a project.
    Requires auth. Verifies project belongs to current user.
    """
    stmt = select(Project).where(Project.id == analysis_data.project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    # Ownership check: you can only create analyses in your own projects
    if project.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to create analyses in this project"
        )

    analysis = Analysis(
        project_id=analysis_data.project_id,
        status="queued",
        input_type=analysis_data.input_type,
        hla_provided=analysis_data.hla_provided,
    )
    db.add(analysis)
    await db.commit()
    await db.refresh(analysis)

    return analysis


@router.get("/", response_model=AnalysisListResponse)
async def list_analyses(
    status_filter: Optional[str] = Query(None, alias="status"),
    project_id: Optional[int] = Query(None),
    input_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisListResponse:
    """
    List all analyses for the current user across all their projects.
    Supports filtering by status, project_id, and input_type.
    Returns newest first, with project name and result counts.
    """
    # Base condition: user's projects only
    base_cond = Project.user_id == current_user.id

    # Build filters
    filters = [base_cond]
    if status_filter is not None:
        filters.append(Analysis.status == status_filter)
    if project_id is not None:
        filters.append(Analysis.project_id == project_id)
    if input_type is not None:
        filters.append(Analysis.input_type == input_type)

    # Count
    count_stmt = (
        select(func.count(Analysis.id))
        .join(Project, Analysis.project_id == Project.id)
        .where(*filters)
    )
    total = (await db.execute(count_stmt)).scalar() or 0

    # Fetch with project info + counts via subqueries
    variant_sub = (
        select(func.count(Variant.id))
        .where(Variant.analysis_id == Analysis.id)
        .correlate(Analysis)
        .scalar_subquery()
    )
    epitope_sub = (
        select(func.count(Epitope.id))
        .where(Epitope.analysis_id == Analysis.id)
        .correlate(Analysis)
        .scalar_subquery()
    )

    stmt = (
        select(
            Analysis,
            Project.name.label("project_name"),
            Project.cancer_type.label("cancer_type"),
            variant_sub.label("variant_count"),
            epitope_sub.label("epitope_count"),
        )
        .join(Project, Analysis.project_id == Project.id)
        .where(*filters)
        .order_by(Analysis.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    analyses = []
    for row in rows:
        a = row[0]  # Analysis object
        analyses.append(AnalysisResponse(
            id=a.id,
            project_id=a.project_id,
            status=a.status,
            input_type=a.input_type,
            hla_provided=a.hla_provided,
            isambard_job_id=a.isambard_job_id,
            created_at=a.created_at,
            completed_at=a.completed_at,
            project_name=row[1],
            cancer_type=row[2],
            variant_count=row[3],
            epitope_count=row[4],
        ))

    return AnalysisListResponse(analyses=analyses, total=total)


# -- Dashboard stats -- must be before /{analysis_id} to avoid path collision


class DashboardStatsResponse(BaseModel):
    """Aggregate stats for the dashboard."""
    total_projects: int
    total_analyses: int
    active_analyses: int  # queued + running
    total_epitopes: int
    recent_analyses: List[AnalysisResponse]


@router.get("/stats/dashboard", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DashboardStatsResponse:
    """
    Aggregate stats for the user's dashboard.
    One endpoint to avoid N+1 fetches on the home page.
    """
    user_projects = select(Project.id).where(Project.user_id == current_user.id)

    total_projects = (await db.execute(
        select(func.count(Project.id)).where(Project.user_id == current_user.id)
    )).scalar() or 0

    total_analyses = (await db.execute(
        select(func.count(Analysis.id)).where(Analysis.project_id.in_(user_projects))
    )).scalar() or 0

    active_analyses = (await db.execute(
        select(func.count(Analysis.id))
        .where(Analysis.project_id.in_(user_projects))
        .where(Analysis.status.in_(["queued", "running"]))
    )).scalar() or 0

    total_epitopes = (await db.execute(
        select(func.count(Epitope.id))
        .where(Epitope.analysis_id.in_(
            select(Analysis.id).where(Analysis.project_id.in_(user_projects))
        ))
    )).scalar() or 0

    # 5 most recent analyses with project info
    variant_sub = (
        select(func.count(Variant.id))
        .where(Variant.analysis_id == Analysis.id)
        .correlate(Analysis)
        .scalar_subquery()
    )
    epitope_sub = (
        select(func.count(Epitope.id))
        .where(Epitope.analysis_id == Analysis.id)
        .correlate(Analysis)
        .scalar_subquery()
    )
    recent_stmt = (
        select(
            Analysis,
            Project.name.label("project_name"),
            Project.cancer_type.label("cancer_type"),
            variant_sub.label("variant_count"),
            epitope_sub.label("epitope_count"),
        )
        .join(Project, Analysis.project_id == Project.id)
        .where(Project.user_id == current_user.id)
        .order_by(Analysis.created_at.desc())
        .limit(5)
    )
    rows = (await db.execute(recent_stmt)).all()

    recent = [
        AnalysisResponse(
            id=row[0].id,
            project_id=row[0].project_id,
            status=row[0].status,
            input_type=row[0].input_type,
            hla_provided=row[0].hla_provided,
            isambard_job_id=row[0].isambard_job_id,
            created_at=row[0].created_at,
            completed_at=row[0].completed_at,
            project_name=row[1],
            cancer_type=row[2],
            variant_count=row[3],
            epitope_count=row[4],
        )
        for row in rows
    ]

    return DashboardStatsResponse(
        total_projects=total_projects,
        total_analyses=total_analyses,
        active_analyses=active_analyses,
        total_epitopes=total_epitopes,
        recent_analyses=recent,
    )


@router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisResponse:
    """Get analysis details by ID. Requires auth + ownership."""
    analysis = await _get_analysis_with_ownership(analysis_id, current_user, db)

    # Compute per-project sequential number (ordered by created_at).
    # "Analysis #2 of 5" is more useful to users than "Analysis #37".
    numbering_stmt = (
        select(Analysis.id)
        .where(Analysis.project_id == analysis.project_id)
        .order_by(Analysis.created_at.asc())
    )
    numbering_result = await db.execute(numbering_stmt)
    project_analysis_ids = [r[0] for r in numbering_result.all()]

    response = AnalysisResponse.model_validate(analysis)
    try:
        response.project_analysis_number = project_analysis_ids.index(analysis.id) + 1
    except ValueError:
        response.project_analysis_number = 1
    response.project_analysis_total = len(project_analysis_ids)

    # Also populate project info
    project_stmt = select(Project).where(Project.id == analysis.project_id)
    project_result = await db.execute(project_stmt)
    project = project_result.scalar_one_or_none()
    if project:
        response.project_name = project.name
        response.cancer_type = project.cancer_type

    return response


@router.get("/{analysis_id}/status", response_model=AnalysisStatusResponse)
async def get_analysis_status(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisStatusResponse:
    """
    Get analysis status including job progress, pipeline steps, and result counts.
    Requires auth + ownership.

    Returns:
      - pipeline_steps: ordered list with status of each step (pending/running/complete/failed)
      - progress_pct: overall progress 0.0-1.0
      - job_progress: raw log entries (backward compat)
    """
    analysis = await _get_analysis_with_ownership(analysis_id, current_user, db)

    job_logs_stmt = select(JobLog).where(
        JobLog.analysis_id == analysis_id
    ).order_by(JobLog.timestamp)
    job_logs_result = await db.execute(job_logs_stmt)
    job_logs = job_logs_result.scalars().all()

    job_progress = [
        JobProgressItem(
            step=log.step,
            status=log.status,
            message=log.message,
            timestamp=log.timestamp,
        )
        for log in job_logs
    ]

    # Build per-step status map from logs (last status wins per step)
    step_status_map: dict[str, dict] = {}
    for log in job_logs:
        step_status_map[log.step] = {
            "status": log.status,
            "message": log.message,
            "timestamp": log.timestamp,
        }

    # Build pipeline_steps list with status
    pipeline_steps = []
    for step_def in PIPELINE_STEPS:
        info = step_status_map.get(step_def["key"])
        if info:
            pipeline_steps.append(PipelineStepStatus(
                key=step_def["key"],
                label=step_def["label"],
                status=info["status"],
                message=info["message"],
                timestamp=info["timestamp"],
            ))
        else:
            pipeline_steps.append(PipelineStepStatus(
                key=step_def["key"],
                label=step_def["label"],
                status="pending",
            ))

    # Calculate progress_pct from completed steps
    progress_pct = 0.0
    if analysis.status == "complete":
        progress_pct = 1.0
    elif analysis.status == "failed":
        # Show progress up to last completed step
        for log in job_logs:
            if log.status == "complete" and log.step in _CUMULATIVE:
                progress_pct = max(progress_pct, _CUMULATIVE[log.step])
    else:
        for log in job_logs:
            if log.step in _CUMULATIVE:
                if log.status == "complete":
                    progress_pct = max(progress_pct, _CUMULATIVE[log.step])
                elif log.status == "running":
                    # Halfway into step
                    idx = _STEP_KEYS.index(log.step)
                    prev = _CUMULATIVE[_STEP_KEYS[idx - 1]] if idx > 0 else 0.0
                    progress_pct = max(progress_pct, (prev + _CUMULATIVE[log.step]) / 2)

    # Count variants and epitopes
    variant_count_stmt = select(func.count(Variant.id)).where(
        Variant.analysis_id == analysis_id
    )
    variant_count = (await db.execute(variant_count_stmt)).scalar() or 0

    epitope_count_stmt = select(func.count(Epitope.id)).where(
        Epitope.analysis_id == analysis_id
    )
    epitope_count = (await db.execute(epitope_count_stmt)).scalar() or 0

    return AnalysisStatusResponse(
        analysis_id=analysis.id,
        status=analysis.status,
        progress_pct=round(progress_pct, 3),
        pipeline_steps=pipeline_steps,
        job_progress=job_progress,
        variant_count=variant_count,
        epitope_count=epitope_count,
        updated_at=analysis.completed_at or analysis.created_at,
    )


# -- HLA allele validation --

# Matches HLA-A*02:01, HLA-B*44:02, HLA-C*05:01, etc.
# Also accepts without HLA- prefix: A*02:01
HLA_PATTERN = re.compile(
    r"^(HLA-)?[ABC]\*\d{2,3}:\d{2,3}$"
)


def _normalize_hla(allele: str) -> str:
    """Normalize HLA allele to HLA-X*NN:NN format."""
    allele = allele.strip().upper()
    if not allele.startswith("HLA-"):
        allele = f"HLA-{allele}"
    return allele


class SubmitAnalysisRequest(BaseModel):
    """
    Request to submit an analysis for processing.
    Called after files are uploaded. Stores HLA alleles and kicks off
    the Celery pipeline task.
    """
    hla_alleles: Optional[List[str]] = None

    @field_validator("hla_alleles")
    @classmethod
    def validate_hla_alleles(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        if len(v) == 0:
            return None
        if len(v) > 6:
            raise ValueError("Maximum 6 HLA alleles (2 per locus: A, B, C)")

        normalized = []
        for allele in v:
            norm = _normalize_hla(allele)
            if not HLA_PATTERN.match(norm):
                raise ValueError(
                    f"Invalid HLA allele format: '{allele}'. "
                    f"Expected format: HLA-A*02:01"
                )
            normalized.append(norm)

        # Enforce max 2 alleles per locus (humans are diploid)
        from collections import Counter
        locus_counts = Counter(a.split("*")[0] for a in normalized)  # e.g. "HLA-A"
        for locus, count in locus_counts.items():
            if count > 2:
                raise ValueError(
                    f"Too many alleles for {locus} ({count}). Maximum 2 per locus (diploid)."
                )

        return normalized


class SubmitAnalysisResponse(BaseModel):
    """Response after submitting analysis for processing."""
    analysis_id: int
    status: str
    message: str


@router.post("/{analysis_id}/submit", response_model=SubmitAnalysisResponse)
async def submit_analysis(
    analysis_id: int,
    data: SubmitAnalysisRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubmitAnalysisResponse:
    """
    Submit an analysis for processing.

    Call this after uploading files. It:
    1. Validates the analysis has at least one uploaded file
    2. Stores HLA alleles if provided
    3. Dispatches the Celery pipeline task
    4. Sets status to 'queued'
    """
    analysis = await _get_analysis_with_ownership(analysis_id, current_user, db)

    if analysis.status != "queued":
        raise HTTPException(
            status_code=400,
            detail=f"Analysis already in '{analysis.status}' state",
        )

    # Check that at least one file was uploaded
    inputs_stmt = select(func.count(AnalysisInput.id)).where(
        AnalysisInput.analysis_id == analysis_id
    )
    input_count = (await db.execute(inputs_stmt)).scalar() or 0

    if input_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No files uploaded. Upload at least one file before submitting.",
        )

    # Store HLA alleles
    hla_provided = False
    if data.hla_alleles:
        hla_provided = True
        for allele in data.hla_alleles:
            hla_type = HLAType(
                analysis_id=analysis_id,
                allele=allele,
                source="provided",
            )
            db.add(hla_type)

    analysis.hla_provided = hla_provided
    await db.commit()

    # Check for expression matrix upload and parse it before dispatching.
    # This runs at submit time so the Celery task gets a plain dict (JSON-serializable).
    expression_data: dict[str, float] | None = None
    expr_stmt = select(AnalysisInput).where(
        AnalysisInput.analysis_id == analysis_id,
        AnalysisInput.file_type == "expression_matrix",
    )
    expr_result = await db.execute(expr_stmt)
    expr_input = expr_result.scalar_one_or_none()

    if expr_input:
        try:
            expression_data = parse_expression_matrix(expr_input.file_path)
            logger.info(
                f"Loaded expression data for analysis {analysis_id}: "
                f"{len(expression_data)} genes"
            )
        except ExpressionParseError as e:
            logger.warning(
                f"Expression matrix parse failed for analysis {analysis_id}: {e}. "
                "Proceeding without expression data."
            )
            expression_data = None

    # Dispatch Celery task
    dispatch_ok = True
    try:
        from app.celery_app import process_vcf_analysis
        task = process_vcf_analysis.delay(
            analysis_id,
            expression_data=expression_data,
        )
        logger.info(f"Dispatched pipeline task {task.id} for analysis {analysis_id}")
        # Store task ID in Redis for cancel support
        try:
            await store_celery_task_id(analysis_id, task.id)
        except Exception:
            # Non-critical -- cancel just won't work if this fails
            logger.warning(f"Failed to store Celery task ID for analysis {analysis_id}")
    except Exception as e:
        dispatch_ok = False
        logger.error(f"Failed to dispatch Celery task for analysis {analysis_id}: {e}")

    message = "Analysis submitted for processing"
    if not dispatch_ok:
        message = (
            "Analysis saved but job queue is unavailable. "
            "Processing will start automatically when the queue recovers."
        )

    logger.info(
        f"Analysis {analysis_id} submitted: hla_provided={hla_provided}, "
        f"dispatch_ok={dispatch_ok}"
    )

    return SubmitAnalysisResponse(
        analysis_id=analysis_id,
        status="queued",
        message=message,
    )


# -- Cancel / Retry endpoints --


class CancelResponse(BaseModel):
    analysis_id: int
    status: str
    message: str


@router.post("/{analysis_id}/cancel", response_model=CancelResponse)
async def cancel_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CancelResponse:
    """
    Cancel a running or queued analysis.

    Revokes the Celery task (if we have the task ID) and sets status to 'cancelled'.
    Already-completed or already-failed analyses cannot be cancelled.

    Uses a conditional UPDATE to avoid TOCTOU race: if the pipeline completes
    between the check and update, the WHERE clause won't match and we detect it.
    """
    analysis = await _get_analysis_with_ownership(analysis_id, current_user, db)

    if analysis.status in ("complete", "failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel analysis in '{analysis.status}' state",
        )

    # Try to revoke the Celery task
    task_id = await get_celery_task_id(analysis_id)
    if task_id:
        try:
            from app.celery_app import celery_app
            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
            logger.info(f"Revoked Celery task {task_id} for analysis {analysis_id}")
        except Exception as e:
            logger.warning(f"Failed to revoke Celery task {task_id}: {e}")

    # Conditional UPDATE: only cancel if still in a cancellable state.
    # Prevents overwriting a concurrent completion.
    from sqlalchemy import update
    result = await db.execute(
        update(Analysis)
        .where(Analysis.id == analysis_id)
        .where(Analysis.status.in_(["queued", "running"]))
        .values(status="cancelled")
    )
    await db.commit()

    if result.rowcount == 0:  # type: ignore[union-attr]
        # Status changed between check and update (pipeline finished)
        await db.refresh(analysis)
        raise HTTPException(
            status_code=409,
            detail=f"Analysis status changed to '{analysis.status}' before cancel took effect",
        )

    # Publish terminal event to WebSocket clients
    from app.pipeline.progress import publish_terminal
    await publish_terminal(analysis_id, "cancelled", "Analysis cancelled by user")

    return CancelResponse(
        analysis_id=analysis_id,
        status="cancelled",
        message="Analysis cancelled",
    )


class RetryResponse(BaseModel):
    analysis_id: int
    status: str
    message: str


@router.post("/{analysis_id}/retry", response_model=RetryResponse)
async def retry_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RetryResponse:
    """
    Retry a failed or cancelled analysis.

    Resets status to 'queued', clears old results (variants, epitopes, logs),
    and re-dispatches the Celery task.
    """
    analysis = await _get_analysis_with_ownership(analysis_id, current_user, db)

    if analysis.status not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry failed or cancelled analyses (current: '{analysis.status}')",
        )

    # Check files still exist
    inputs_stmt = select(func.count(AnalysisInput.id)).where(
        AnalysisInput.analysis_id == analysis_id
    )
    input_count = (await db.execute(inputs_stmt)).scalar() or 0
    if input_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No input files found. Cannot retry without files.",
        )

    # Revoke old Celery task if still lingering (e.g. in retry queue)
    old_task_id = await get_celery_task_id(analysis_id)
    if old_task_id:
        try:
            from app.celery_app import celery_app as _celery
            _celery.control.revoke(old_task_id, terminate=True, signal="SIGTERM")
            logger.info(f"Retry: revoked old task {old_task_id} for analysis {analysis_id}")
        except Exception as e:
            logger.warning(f"Retry: failed to revoke old task {old_task_id}: {e}")

    # Clear old results: epitopes first (FK to variants), then variants, then logs
    from sqlalchemy import delete

    await db.execute(delete(Epitope).where(Epitope.analysis_id == analysis_id))
    await db.execute(delete(Variant).where(Variant.analysis_id == analysis_id))
    await db.execute(delete(JobLog).where(JobLog.analysis_id == analysis_id))

    # Reset analysis state
    analysis.status = "queued"
    analysis.completed_at = None
    await db.commit()

    # Load expression data if available (same pattern as submit endpoint)
    retry_expression_data: dict[str, float] | None = None
    retry_expr_stmt = select(AnalysisInput).where(
        AnalysisInput.analysis_id == analysis_id,
        AnalysisInput.file_type == "expression_matrix",
    )
    retry_expr_result = await db.execute(retry_expr_stmt)
    retry_expr_input = retry_expr_result.scalar_one_or_none()
    if retry_expr_input:
        try:
            retry_expression_data = parse_expression_matrix(retry_expr_input.file_path)
        except ExpressionParseError:
            logger.warning(f"Retry: expression matrix parse failed for analysis {analysis_id}")

    # Re-dispatch Celery task
    dispatch_ok = True
    try:
        from app.celery_app import process_vcf_analysis
        task = process_vcf_analysis.delay(
            analysis_id,
            expression_data=retry_expression_data,
        )
        logger.info(f"Retry: dispatched task {task.id} for analysis {analysis_id}")
        try:
            await store_celery_task_id(analysis_id, task.id)
        except Exception:
            logger.warning(f"Retry: failed to store task ID for analysis {analysis_id}")
    except Exception as e:
        dispatch_ok = False
        logger.error(f"Retry: failed to dispatch task for analysis {analysis_id}: {e}")

    msg = "Analysis re-queued for processing"
    if not dispatch_ok:
        msg = "Analysis reset but job queue unavailable. Will start when queue recovers."

    return RetryResponse(
        analysis_id=analysis_id,
        status="queued",
        message=msg,
    )


# -- Clone endpoint --


class CloneResponse(BaseModel):
    analysis_id: int
    status: str
    message: str


@router.post("/{analysis_id}/clone", response_model=CloneResponse, status_code=status.HTTP_201_CREATED)
async def clone_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CloneResponse:
    """
    Clone an analysis: creates a new analysis in the same project
    with the same settings (input_type, HLA alleles) and input files.
    Does NOT re-run automatically -- caller must submit separately.

    Useful for re-running with different parameters or after uploading
    additional files.
    """
    analysis = await _get_analysis_with_ownership(analysis_id, current_user, db)

    # Load HLA alleles and input files from the original
    hla_stmt = select(HLAType).where(HLAType.analysis_id == analysis_id)
    hla_types = (await db.execute(hla_stmt)).scalars().all()

    inputs_stmt = select(AnalysisInput).where(AnalysisInput.analysis_id == analysis_id)
    inputs = (await db.execute(inputs_stmt)).scalars().all()

    # Create new analysis
    new_analysis = Analysis(
        project_id=analysis.project_id,
        status="queued",
        input_type=analysis.input_type,
        hla_provided=analysis.hla_provided,
    )
    db.add(new_analysis)
    await db.flush()  # get new_analysis.id

    # Copy HLA alleles
    for hla in hla_types:
        db.add(HLAType(
            analysis_id=new_analysis.id,
            allele=hla.allele,
            source=hla.source,
        ))

    # Copy input file references (same paths, not duplicating actual files)
    for inp in inputs:
        db.add(AnalysisInput(
            analysis_id=new_analysis.id,
            file_type=inp.file_type,
            file_path=inp.file_path,
            file_size=inp.file_size,
            checksum=inp.checksum,
        ))

    await db.commit()
    await db.refresh(new_analysis)

    logger.info(f"Cloned analysis {analysis_id} -> {new_analysis.id}")

    return CloneResponse(
        analysis_id=new_analysis.id,
        status="queued",
        message=f"Cloned from analysis #{analysis_id}. Submit to start processing.",
    )
