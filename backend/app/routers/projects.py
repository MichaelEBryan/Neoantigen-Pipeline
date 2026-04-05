from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime
from app.database import get_db
from app.models import Project, Analysis, User
from app.auth import get_current_user

router = APIRouter()


# -- Request/response models --

class ProjectCreate(BaseModel):
    """Create a new project."""
    name: str
    cancer_type: str
    stage: Optional[str] = None  # I, II, III, IV
    reference_genome: str = "GRCh38"


class ProjectUpdate(BaseModel):
    """Update an existing project. All fields optional."""
    name: Optional[str] = None
    cancer_type: Optional[str] = None
    stage: Optional[str] = None
    reference_genome: Optional[str] = None


class AnalysisCountsByStatus(BaseModel):
    """Breakdown of analysis counts by status within a project."""
    queued: int = 0
    running: int = 0
    complete: int = 0
    failed: int = 0
    cancelled: int = 0


class ProjectResponse(BaseModel):
    """Project details returned to client."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    cancer_type: str
    stage: Optional[str]
    reference_genome: str
    created_at: datetime
    analysis_count: int = 0
    status_counts: Optional[AnalysisCountsByStatus] = None


class ProjectListResponse(BaseModel):
    """Paginated list of projects."""
    projects: List[ProjectResponse]
    total: int


# -- Endpoints --

@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    """Create a new project for the authenticated user."""
    project = Project(
        user_id=current_user.id,
        name=data.name,
        cancer_type=data.cancer_type,
        stage=data.stage,
        reference_genome=data.reference_genome,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        name=project.name,
        cancer_type=project.cancer_type,
        stage=project.stage,
        reference_genome=project.reference_genome,
        created_at=project.created_at,
        analysis_count=0,
    )


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectListResponse:
    """List all projects for the authenticated user."""
    # Get total count
    from sqlalchemy import func
    count_stmt = select(func.count(Project.id)).where(Project.user_id == current_user.id)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Fetch projects with eager-loaded analyses for count
    stmt = (
        select(Project)
        .where(Project.user_id == current_user.id)
        .options(selectinload(Project.analyses))
        .order_by(Project.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    projects = result.scalars().all()

    project_responses = []
    for p in projects:
        # Count analyses per status
        counts = AnalysisCountsByStatus()
        for a in p.analyses:
            if a.status == "queued":
                counts.queued += 1
            elif a.status == "running":
                counts.running += 1
            elif a.status == "complete":
                counts.complete += 1
            elif a.status == "failed":
                counts.failed += 1
            elif a.status == "cancelled":
                counts.cancelled += 1

        project_responses.append(ProjectResponse(
            id=p.id,
            user_id=p.user_id,
            name=p.name,
            cancer_type=p.cancer_type,
            stage=p.stage,
            reference_genome=p.reference_genome,
            created_at=p.created_at,
            analysis_count=len(p.analyses),
            status_counts=counts,
        ))

    return ProjectListResponse(projects=project_responses, total=total)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    """Get a single project by ID. Must belong to authenticated user."""
    stmt = (
        select(Project)
        .options(selectinload(Project.analyses))
        .where(Project.id == project_id)
    )
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if project.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        name=project.name,
        cancer_type=project.cancer_type,
        stage=project.stage,
        reference_genome=project.reference_genome,
        created_at=project.created_at,
        analysis_count=len(project.analyses),
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    """Update a project. Only the owner can update."""
    stmt = (
        select(Project)
        .options(selectinload(Project.analyses))
        .where(Project.id == project_id)
    )
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if project.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    # Apply only the fields that were actually sent
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    await db.commit()
    await db.refresh(project)

    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        name=project.name,
        cancer_type=project.cancer_type,
        stage=project.stage,
        reference_genome=project.reference_genome,
        created_at=project.created_at,
        analysis_count=len(project.analyses),
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Delete a project and all its analyses (cascade).
    Only the owner can delete.
    """
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if project.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    await db.delete(project)
    await db.commit()
