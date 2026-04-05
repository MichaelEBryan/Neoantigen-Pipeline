import logging
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    User, Project, Analysis, AnalysisInput, HLAType,
    Variant, Epitope, JobLog,
)
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user
)

logger = logging.getLogger(__name__)


router = APIRouter()


# Pydantic request/response schemas

class UserRegisterRequest(BaseModel):
    """Schema for user registration."""
    email: EmailStr
    password: str
    name: str
    institution: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password meets minimum requirements."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UserLoginRequest(BaseModel):
    """Schema for user login."""
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """Schema for user info response."""
    id: int
    email: str
    name: str
    institution: Optional[str]
    is_admin: bool = False
    created_at: datetime
    terms_accepted: bool
    terms_accepted_at: Optional[datetime] = None

    @staticmethod
    def from_user(user: User) -> "UserResponse":
        """Convert User model to response schema."""
        return UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            institution=user.institution,
            is_admin=user.is_admin,
            created_at=user.created_at,
            terms_accepted=user.terms_accepted_at is not None,
            terms_accepted_at=user.terms_accepted_at,
        )


class TokenResponse(BaseModel):
    """Schema for token response."""
    access_token: str
    token_type: str = "bearer"


class AuthResponse(BaseModel):
    """Combined response with user info and token."""
    user: UserResponse
    access_token: str
    token_type: str = "bearer"


# Endpoints

@router.post("/register", status_code=201, response_model=AuthResponse)
async def register(
    request: UserRegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new user account.

    Validates email format, password strength, and email uniqueness.
    Hashes password and creates user in database.
    Returns user info and JWT access token.
    """
    # Check if email already exists
    stmt = select(User).where(User.email == request.email)
    result = await db.execute(stmt)
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )

    # Create new user
    hashed_pwd = hash_password(request.password)
    new_user = User(
        email=request.email,
        name=request.name,
        institution=request.institution,
        hashed_password=hashed_pwd
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Create JWT token
    access_token = create_access_token(
        data={"user_id": new_user.id, "email": new_user.email}
    )

    user_response = UserResponse.from_user(new_user)

    return AuthResponse(
        user=user_response,
        access_token=access_token,
        token_type="bearer"
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: UserLoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticate user with email and password.

    Returns user info and JWT access token on success.
    Returns 401 if credentials are invalid.
    """
    # Find user by email
    stmt = select(User).where(User.email == request.email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Update last login timestamp
    try:
        user.last_login_at = datetime.now(timezone.utc)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    except Exception:
        # If last_login_at column doesn't exist yet (migration 003 not applied),
        # roll back and continue without updating it.
        await db.rollback()
        # Re-fetch user after rollback so the session is clean
        result = await db.execute(select(User).where(User.id == user.id))
        user = result.scalar_one()

    # Create JWT token
    access_token = create_access_token(
        data={"user_id": user.id, "email": user.email}
    )

    user_response = UserResponse.from_user(user)

    return AuthResponse(
        user=user_response,
        access_token=access_token,
        token_type="bearer"
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Get current authenticated user info.

    Requires valid JWT in Authorization header.
    """
    return UserResponse.from_user(current_user)


@router.post("/accept-terms", response_model=UserResponse)
async def accept_terms(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Record that user accepted terms and conditions.

    Updates terms_accepted_at timestamp on user record.
    Requires valid JWT in Authorization header.
    """
    current_user.terms_accepted_at = datetime.now(timezone.utc)
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)

    return UserResponse.from_user(current_user)


# -- GDPR Data Export --


@router.get("/export-my-data")
async def export_my_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Export all user data as a JSON archive. Covers:
      - Account info (no password hash)
      - Projects
      - Analyses with inputs, HLA types, variants, epitopes, job logs

    Returns a JSON response with Content-Disposition header so browsers
    download it as a file.
    """
    projects_stmt = (
        select(Project)
        .where(Project.user_id == current_user.id)
        .options(
            selectinload(Project.analyses)
            .selectinload(Analysis.inputs),
            selectinload(Project.analyses)
            .selectinload(Analysis.hla_types),
            selectinload(Project.analyses)
            .selectinload(Analysis.variants)
            .selectinload(Variant.epitopes),
            selectinload(Project.analyses)
            .selectinload(Analysis.job_logs),
        )
        .order_by(Project.created_at)
    )
    result = await db.execute(projects_stmt)
    projects = result.scalars().unique().all()

    def _dt(d: Optional[datetime]) -> Optional[str]:
        return d.isoformat() if d else None

    export: dict[str, Any] = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": {
            "id": current_user.id,
            "email": current_user.email,
            "name": current_user.name,
            "institution": current_user.institution,
            "created_at": _dt(current_user.created_at),
            "terms_accepted_at": _dt(current_user.terms_accepted_at),
        },
        "projects": [],
    }

    for proj in projects:
        proj_data: dict[str, Any] = {
            "id": proj.id,
            "name": proj.name,
            "cancer_type": proj.cancer_type,
            "stage": proj.stage,
            "reference_genome": proj.reference_genome,
            "created_at": _dt(proj.created_at),
            "analyses": [],
        }

        for analysis in proj.analyses:
            a_data: dict[str, Any] = {
                "id": analysis.id,
                "status": analysis.status,
                "input_type": analysis.input_type,
                "hla_provided": analysis.hla_provided,
                "created_at": _dt(analysis.created_at),
                "completed_at": _dt(analysis.completed_at),
                "inputs": [
                    {
                        "file_type": inp.file_type,
                        "file_path": inp.file_path,
                        "file_size": inp.file_size,
                        "checksum": inp.checksum,
                    }
                    for inp in analysis.inputs
                ],
                "hla_types": [
                    {"allele": h.allele, "source": h.source}
                    for h in analysis.hla_types
                ],
                "variants": [],
                "job_logs": [
                    {
                        "step": log.step,
                        "status": log.status,
                        "message": log.message,
                        "timestamp": _dt(log.timestamp),
                    }
                    for log in analysis.job_logs
                ],
            }

            for var in analysis.variants:
                v_data: dict[str, Any] = {
                    "chrom": var.chrom,
                    "pos": var.pos,
                    "ref": var.ref,
                    "alt": var.alt,
                    "gene": var.gene,
                    "protein_change": var.protein_change,
                    "variant_type": var.variant_type,
                    "vaf": var.vaf,
                    "epitopes": [
                        {
                            "peptide_seq": ep.peptide_seq,
                            "peptide_length": ep.peptide_length,
                            "hla_allele": ep.hla_allele,
                            "binding_affinity_nm": ep.binding_affinity_nm,
                            "presentation_score": ep.presentation_score,
                            "processing_score": ep.processing_score,
                            "immunogenicity_score": ep.immunogenicity_score,
                            "rank": ep.rank,
                        }
                        for ep in var.epitopes
                    ],
                }
                a_data["variants"].append(v_data)

            proj_data["analyses"].append(a_data)

        export["projects"].append(proj_data)

    logger.info(f"Data export for user {current_user.id} ({current_user.email})")

    return JSONResponse(
        content=export,
        headers={
            "Content-Disposition": f'attachment; filename="oxford-cvd-export-{current_user.id}.json"',
        },
    )


# -- GDPR Delete Account --


class DeleteAccountResponse(BaseModel):
    message: str


@router.delete("/delete-my-account", response_model=DeleteAccountResponse)
async def delete_my_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeleteAccountResponse:
    """
    Permanently delete the user's account and ALL associated data.

    Cascade order (respecting FK constraints):
      epitopes -> variants -> job_logs -> hla_types -> analysis_inputs
      -> analyses -> projects -> user

    This is irreversible. The frontend should confirm before calling.
    """
    user_id = current_user.id
    email = current_user.email

    # Get all project IDs
    proj_ids_stmt = select(Project.id).where(Project.user_id == user_id)
    proj_ids = [r[0] for r in (await db.execute(proj_ids_stmt)).all()]

    if proj_ids:
        # Get all analysis IDs
        analysis_ids_stmt = select(Analysis.id).where(Analysis.project_id.in_(proj_ids))
        analysis_ids = [r[0] for r in (await db.execute(analysis_ids_stmt)).all()]

        if analysis_ids:
            # Get all variant IDs (for epitope FK)
            variant_ids_stmt = select(Variant.id).where(Variant.analysis_id.in_(analysis_ids))
            variant_ids = [r[0] for r in (await db.execute(variant_ids_stmt)).all()]

            # Delete in FK order
            if variant_ids:
                await db.execute(delete(Epitope).where(Epitope.variant_id.in_(variant_ids)))
            await db.execute(delete(Variant).where(Variant.analysis_id.in_(analysis_ids)))
            await db.execute(delete(JobLog).where(JobLog.analysis_id.in_(analysis_ids)))
            await db.execute(delete(HLAType).where(HLAType.analysis_id.in_(analysis_ids)))
            await db.execute(delete(AnalysisInput).where(AnalysisInput.analysis_id.in_(analysis_ids)))

        await db.execute(delete(Analysis).where(Analysis.project_id.in_(proj_ids)))
        await db.execute(delete(Project).where(Project.id.in_(proj_ids)))

    # Delete the user
    await db.execute(delete(User).where(User.id == user_id))
    await db.commit()

    logger.info(f"Account deleted: user {user_id} ({email})")

    return DeleteAccountResponse(
        message="Your account and all associated data have been permanently deleted.",
    )
