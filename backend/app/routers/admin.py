"""
Admin router -- platform-wide visibility for admins.

All endpoints require is_admin=True on the authenticated user.
Default admin is seeded in migration 002 (michael.bryan@new.ox.ac.uk).

Provides:
  - User listing with stats
  - Project listing (all users)
  - File/upload listing (all users)
  - Platform-wide aggregate stats
  - Toggle admin on/off for other users
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, update
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import User, Project, Analysis, AnalysisInput, Variant, Epitope
from app.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# -- Response schemas --


class AdminUserRow(BaseModel):
    id: int
    email: str
    name: str
    institution: Optional[str]
    is_admin: bool
    created_at: datetime
    last_login_at: Optional[datetime]
    terms_accepted_at: Optional[datetime]
    project_count: int
    analysis_count: int
    epitope_count: int


class AdminUserProject(BaseModel):
    id: int
    name: str
    cancer_type: str
    analysis_count: int
    created_at: datetime


class AdminUserDetail(BaseModel):
    id: int
    email: str
    name: str
    institution: Optional[str]
    is_admin: bool
    created_at: datetime
    last_login_at: Optional[datetime]
    terms_accepted_at: Optional[datetime]
    project_count: int
    analysis_count: int
    epitope_count: int
    variant_count: int
    total_upload_bytes: int
    projects: List[AdminUserProject]


class AdminUserListResponse(BaseModel):
    users: List[AdminUserRow]
    total: int


class AdminProjectRow(BaseModel):
    id: int
    name: str
    cancer_type: str
    stage: Optional[str]
    reference_genome: str
    created_at: datetime
    owner_email: str
    owner_name: str
    analysis_count: int
    status_breakdown: dict  # e.g. {"queued": 1, "complete": 2}


class AdminProjectListResponse(BaseModel):
    projects: List[AdminProjectRow]
    total: int


class AdminFileRow(BaseModel):
    id: int
    analysis_id: int
    project_name: str
    owner_email: str
    file_type: str
    file_path: str
    file_size: Optional[int]
    checksum: Optional[str]
    analysis_status: str
    created_at: datetime


class AdminFileListResponse(BaseModel):
    files: List[AdminFileRow]
    total: int


class PlatformStats(BaseModel):
    total_users: int
    total_projects: int
    total_analyses: int
    analyses_by_status: dict  # {"queued": N, "running": N, ...}
    total_variants: int
    total_epitopes: int
    total_upload_bytes: int  # sum of all file sizes
    total_upload_files: int


class AdminToggleRequest(BaseModel):
    is_admin: bool


class AdminToggleResponse(BaseModel):
    id: int
    email: str
    is_admin: bool


# -- Endpoints --


@router.get("/stats", response_model=PlatformStats)
async def get_platform_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Platform-wide aggregate stats for the admin dashboard."""

    user_count = await db.scalar(select(func.count(User.id)))
    project_count = await db.scalar(select(func.count(Project.id)))
    analysis_count = await db.scalar(select(func.count(Analysis.id)))

    # Status breakdown
    status_rows = await db.execute(
        select(Analysis.status, func.count(Analysis.id))
        .group_by(Analysis.status)
    )
    analyses_by_status = {row[0]: row[1] for row in status_rows.all()}

    variant_count = await db.scalar(select(func.count(Variant.id)))
    epitope_count = await db.scalar(select(func.count(Epitope.id)))

    # Upload stats
    upload_stats = await db.execute(
        select(
            func.count(AnalysisInput.id),
            func.coalesce(func.sum(AnalysisInput.file_size), 0),
        )
    )
    row = upload_stats.one()
    total_files = row[0]
    total_bytes = row[1]

    return PlatformStats(
        total_users=user_count or 0,
        total_projects=project_count or 0,
        total_analyses=analysis_count or 0,
        analyses_by_status=analyses_by_status,
        total_variants=variant_count or 0,
        total_epitopes=epitope_count or 0,
        total_upload_bytes=total_bytes,
        total_upload_files=total_files,
    )


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users with aggregate stats (project, analysis, epitope counts)."""

    # Base query for count
    count_q = select(func.count(User.id))
    if search:
        pattern = f"%{search}%"
        count_q = count_q.where(
            (User.email.ilike(pattern)) | (User.name.ilike(pattern))
        )
    total = await db.scalar(count_q)

    # Main query: user + aggregated stats via subqueries
    # Subquery: project count per user
    proj_sub = (
        select(Project.user_id, func.count(Project.id).label("proj_count"))
        .group_by(Project.user_id)
        .subquery()
    )

    # Subquery: analysis count per user (through projects)
    analysis_sub = (
        select(
            Project.user_id,
            func.count(Analysis.id).label("analysis_count"),
        )
        .join(Analysis, Analysis.project_id == Project.id)
        .group_by(Project.user_id)
        .subquery()
    )

    # Subquery: epitope count per user (through projects -> analyses)
    epitope_sub = (
        select(
            Project.user_id,
            func.count(Epitope.id).label("epitope_count"),
        )
        .join(Analysis, Analysis.project_id == Project.id)
        .join(Epitope, Epitope.analysis_id == Analysis.id)
        .group_by(Project.user_id)
        .subquery()
    )

    q = (
        select(
            User,
            func.coalesce(proj_sub.c.proj_count, 0).label("project_count"),
            func.coalesce(analysis_sub.c.analysis_count, 0).label("analysis_count"),
            func.coalesce(epitope_sub.c.epitope_count, 0).label("epitope_count"),
        )
        .outerjoin(proj_sub, User.id == proj_sub.c.user_id)
        .outerjoin(analysis_sub, User.id == analysis_sub.c.user_id)
        .outerjoin(epitope_sub, User.id == epitope_sub.c.user_id)
        .order_by(User.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    if search:
        pattern = f"%{search}%"
        q = q.where((User.email.ilike(pattern)) | (User.name.ilike(pattern)))

    result = await db.execute(q)
    rows = result.all()

    users = []
    for row in rows:
        user = row[0]
        users.append(AdminUserRow(
            id=user.id,
            email=user.email,
            name=user.name,
            institution=user.institution,
            is_admin=user.is_admin,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            terms_accepted_at=user.terms_accepted_at,
            project_count=row[1],
            analysis_count=row[2],
            epitope_count=row[3],
        ))

    return AdminUserListResponse(users=users, total=total or 0)


@router.patch("/users/{user_id}/admin", response_model=AdminToggleResponse)
async def toggle_admin(
    user_id: int,
    body: AdminToggleRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Grant or revoke admin for a user. Cannot remove your own admin."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own admin status",
        )

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    target = result.scalar_one_or_none()

    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target.is_admin = body.is_admin
    db.add(target)
    await db.commit()
    await db.refresh(target)

    logger.info(
        f"Admin {admin.email} {'granted' if body.is_admin else 'revoked'} "
        f"admin for {target.email}"
    )

    return AdminToggleResponse(
        id=target.id,
        email=target.email,
        is_admin=target.is_admin,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user_detail(
    user_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed info for a specific user including all stats and projects.
    """
    # Fetch user
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Fetch user's projects with analysis counts
    projects_stmt = (
        select(Project)
        .where(Project.user_id == user_id)
        .options(selectinload(Project.analyses))
        .order_by(Project.created_at.desc())
    )
    projects_result = await db.execute(projects_stmt)
    projects_data = projects_result.scalars().unique().all()

    projects_list = [
        AdminUserProject(
            id=p.id,
            name=p.name,
            cancer_type=p.cancer_type,
            analysis_count=len(p.analyses),
            created_at=p.created_at,
        )
        for p in projects_data
    ]

    # Count analyses per user
    analysis_count = await db.scalar(
        select(func.count(Analysis.id))
        .join(Project, Analysis.project_id == Project.id)
        .where(Project.user_id == user_id)
    )

    # Count variants per user
    variant_count = await db.scalar(
        select(func.count(Variant.id))
        .join(Analysis, Variant.analysis_id == Analysis.id)
        .join(Project, Analysis.project_id == Project.id)
        .where(Project.user_id == user_id)
    )

    # Count epitopes per user
    epitope_count = await db.scalar(
        select(func.count(Epitope.id))
        .join(Analysis, Epitope.analysis_id == Analysis.id)
        .join(Project, Analysis.project_id == Project.id)
        .where(Project.user_id == user_id)
    )

    # Sum total upload bytes
    total_bytes_result = await db.execute(
        select(func.coalesce(func.sum(AnalysisInput.file_size), 0))
        .join(Analysis, AnalysisInput.analysis_id == Analysis.id)
        .join(Project, Analysis.project_id == Project.id)
        .where(Project.user_id == user_id)
    )
    total_bytes = total_bytes_result.scalar() or 0

    # Count projects
    project_count = len(projects_list)

    return AdminUserDetail(
        id=user.id,
        email=user.email,
        name=user.name,
        institution=user.institution,
        is_admin=user.is_admin,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        terms_accepted_at=user.terms_accepted_at,
        project_count=project_count,
        analysis_count=analysis_count or 0,
        epitope_count=epitope_count or 0,
        variant_count=variant_count or 0,
        total_upload_bytes=total_bytes,
        projects=projects_list,
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a user and all their data (projects, analyses, files, etc.).
    Cannot delete yourself. Cascading deletes handle related records.
    """
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    target = result.scalar_one_or_none()

    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target_email = target.email

    # Delete user's uploaded files from disk
    try:
        projects_stmt = select(Project).where(Project.user_id == user_id)
        projects_result = await db.execute(projects_stmt)
        user_projects = projects_result.scalars().all()

        for project in user_projects:
            analyses_stmt = select(Analysis).where(Analysis.project_id == project.id)
            analyses_result = await db.execute(analyses_stmt)
            analyses = analyses_result.scalars().all()

            for analysis_obj in analyses:
                inputs_stmt = select(AnalysisInput).where(
                    AnalysisInput.analysis_id == analysis_obj.id
                )
                inputs_result = await db.execute(inputs_stmt)
                inputs = inputs_result.scalars().all()

                for inp in inputs:
                    if inp.file_path and os.path.exists(inp.file_path):
                        try:
                            os.unlink(inp.file_path)
                        except OSError:
                            logger.warning(f"Failed to delete file: {inp.file_path}")
    except Exception as e:
        logger.warning(f"Error cleaning up files for user {user_id}: {e}")

    # Delete the user. SQLAlchemy cascade should handle related records.
    await db.delete(target)
    await db.commit()

    logger.info(f"Admin {admin.email} deleted user {target_email} (id={user_id})")
    return None


@router.get("/projects", response_model=AdminProjectListResponse)
async def list_all_projects(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all projects across all users with analysis counts."""

    count_q = select(func.count(Project.id))
    if search:
        pattern = f"%{search}%"
        count_q = count_q.where(
            (Project.name.ilike(pattern)) | (Project.cancer_type.ilike(pattern))
        )
    total = await db.scalar(count_q)

    q = (
        select(Project)
        .options(selectinload(Project.user), selectinload(Project.analyses))
        .order_by(Project.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    if search:
        pattern = f"%{search}%"
        q = q.where(
            (Project.name.ilike(pattern)) | (Project.cancer_type.ilike(pattern))
        )

    result = await db.execute(q)
    projects = result.scalars().unique().all()

    rows = []
    for p in projects:
        status_map: dict = {}
        for a in p.analyses:
            status_map[a.status] = status_map.get(a.status, 0) + 1

        rows.append(AdminProjectRow(
            id=p.id,
            name=p.name,
            cancer_type=p.cancer_type,
            stage=p.stage,
            reference_genome=p.reference_genome,
            created_at=p.created_at,
            owner_email=p.user.email,
            owner_name=p.user.name,
            analysis_count=len(p.analyses),
            status_breakdown=status_map,
        ))

    return AdminProjectListResponse(projects=rows, total=total or 0)


@router.get("/files", response_model=AdminFileListResponse)
async def list_all_files(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    file_type: Optional[str] = Query(None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all uploaded files across all users."""

    count_q = select(func.count(AnalysisInput.id))
    if file_type:
        count_q = count_q.where(AnalysisInput.file_type == file_type)
    total = await db.scalar(count_q)

    q = (
        select(AnalysisInput, Analysis, Project, User)
        .join(Analysis, AnalysisInput.analysis_id == Analysis.id)
        .join(Project, Analysis.project_id == Project.id)
        .join(User, Project.user_id == User.id)
        .order_by(AnalysisInput.id.desc())
        .offset(skip)
        .limit(limit)
    )
    if file_type:
        q = q.where(AnalysisInput.file_type == file_type)

    result = await db.execute(q)
    rows_raw = result.all()

    files = []
    for inp, analysis, project, user in rows_raw:
        files.append(AdminFileRow(
            id=inp.id,
            analysis_id=inp.analysis_id,
            project_name=project.name,
            owner_email=user.email,
            file_type=inp.file_type,
            file_path=os.path.basename(inp.file_path),  # strip internal path for security
            file_size=inp.file_size,
            checksum=inp.checksum,
            analysis_status=analysis.status,
            created_at=analysis.created_at,
        ))

    return AdminFileListResponse(files=files, total=total or 0)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a project and all its data (analyses, variants, epitopes, uploaded files).
    Cascading deletes handle related records. Removes files from disk.
    """
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    target = result.scalar_one_or_none()

    if not target:
        raise HTTPException(status_code=404, detail="Project not found")

    project_name = target.name
    project_owner_email = None

    # Get owner email for logging
    owner_stmt = select(User).where(User.id == target.user_id)
    owner_result = await db.execute(owner_stmt)
    owner = owner_result.scalar_one_or_none()
    if owner:
        project_owner_email = owner.email

    # Delete uploaded files from disk
    try:
        analyses_stmt = select(Analysis).where(Analysis.project_id == project_id)
        analyses_result = await db.execute(analyses_stmt)
        analyses = analyses_result.scalars().all()

        for analysis_obj in analyses:
            inputs_stmt = select(AnalysisInput).where(
                AnalysisInput.analysis_id == analysis_obj.id
            )
            inputs_result = await db.execute(inputs_stmt)
            inputs = inputs_result.scalars().all()

            for inp in inputs:
                if inp.file_path and os.path.exists(inp.file_path):
                    try:
                        os.unlink(inp.file_path)
                    except OSError:
                        logger.warning(f"Failed to delete file: {inp.file_path}")
    except Exception as e:
        logger.warning(f"Error cleaning up files for project {project_id}: {e}")

    # Delete the project. SQLAlchemy cascade should handle related records.
    await db.delete(target)
    await db.commit()

    logger.info(
        f"Admin {admin.email} deleted project {project_name} "
        f"(id={project_id}, owner={project_owner_email})"
    )
    return None


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Download a user's uploaded file. Admin endpoint for file retrieval.
    Returns file as streaming response with appropriate headers.
    """
    stmt = select(AnalysisInput).where(AnalysisInput.id == file_id)
    result = await db.execute(stmt)
    file_input = result.scalar_one_or_none()

    if not file_input:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = file_input.file_path

    # Verify file exists on disk
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Use basename for the download filename (security: don't leak internal paths)
    filename = os.path.basename(file_path)

    logger.info(f"Admin {admin.email} downloaded file {file_id} ({filename})")

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream",
    )
