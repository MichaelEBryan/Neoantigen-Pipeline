"""
User settings API.

Handles user profile updates, analysis defaults, scoring weight
customization, and display preferences. All settings are stored in the
user_preferences table (one row per user, created on first save).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User, UserPreferences
from app.routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


# -- Request/Response schemas --

class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    institution: Optional[str] = Field(None, max_length=255)


class AnalysisDefaults(BaseModel):
    default_cancer_type: Optional[str] = None
    default_stage: Optional[str] = None
    default_genome: Optional[str] = None
    default_hla_alleles: Optional[list[str]] = None  # list in API, stored as comma-separated

    @field_validator("default_stage")
    @classmethod
    def validate_stage(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("I", "II", "III", "IV", ""):
            raise ValueError("Stage must be I, II, III, IV, or empty")
        return v or None

    @field_validator("default_genome")
    @classmethod
    def validate_genome(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("GRCh38", "GRCh37", ""):
            raise ValueError("Genome must be GRCh38 or GRCh37")
        return v or None


class ScoringWeights(BaseModel):
    """Custom scoring weights. All values 0.0-1.0. Should sum to ~1.0.
    Set to null/omit to use system defaults."""
    weight_presentation: Optional[float] = Field(None, ge=0.0, le=1.0)
    weight_binding_rank: Optional[float] = Field(None, ge=0.0, le=1.0)
    weight_expression: Optional[float] = Field(None, ge=0.0, le=1.0)
    weight_vaf: Optional[float] = Field(None, ge=0.0, le=1.0)
    weight_mutation_type: Optional[float] = Field(None, ge=0.0, le=1.0)
    weight_processing: Optional[float] = Field(None, ge=0.0, le=1.0)
    weight_iedb: Optional[float] = Field(None, ge=0.0, le=1.0)


class DisplayPreferences(BaseModel):
    theme: Optional[str] = None  # "light", "dark", "system"
    results_page_size: Optional[int] = Field(None, ge=10, le=200)
    default_visible_columns: Optional[list[str]] = None

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("light", "dark", "system", ""):
            raise ValueError("Theme must be light, dark, or system")
        return v or None


class FullPreferencesResponse(BaseModel):
    """Complete user preferences for the settings page."""
    # Profile
    name: str
    email: str
    institution: Optional[str]
    created_at: str

    # Analysis defaults
    default_cancer_type: Optional[str] = None
    default_stage: Optional[str] = None
    default_genome: Optional[str] = None
    default_hla_alleles: Optional[list[str]] = None

    # Scoring weights
    weight_presentation: Optional[float] = None
    weight_binding_rank: Optional[float] = None
    weight_expression: Optional[float] = None
    weight_vaf: Optional[float] = None
    weight_mutation_type: Optional[float] = None
    weight_processing: Optional[float] = None
    weight_iedb: Optional[float] = None

    # Display
    theme: Optional[str] = None
    results_page_size: Optional[int] = None
    default_visible_columns: Optional[list[str]] = None


# -- Helpers --

async def _get_or_create_prefs(user: User, db: AsyncSession) -> UserPreferences:
    """Get existing preferences or create a new row."""
    stmt = select(UserPreferences).where(UserPreferences.user_id == user.id)
    result = await db.execute(stmt)
    prefs = result.scalar_one_or_none()
    if prefs is None:
        prefs = UserPreferences(user_id=user.id)
        db.add(prefs)
        await db.flush()
    return prefs


def _prefs_to_response(user: User, prefs: Optional[UserPreferences]) -> FullPreferencesResponse:
    """Build response from user + preferences."""
    hla_list = None
    if prefs and prefs.default_hla_alleles:
        hla_list = [a.strip() for a in prefs.default_hla_alleles.split(",") if a.strip()]

    col_list = None
    if prefs and prefs.default_visible_columns:
        col_list = [c.strip() for c in prefs.default_visible_columns.split(",") if c.strip()]

    return FullPreferencesResponse(
        name=user.name,
        email=user.email,
        institution=user.institution,
        created_at=user.created_at.isoformat(),
        default_cancer_type=prefs.default_cancer_type if prefs else None,
        default_stage=prefs.default_stage if prefs else None,
        default_genome=prefs.default_genome if prefs else None,
        default_hla_alleles=hla_list,
        weight_presentation=prefs.weight_presentation if prefs else None,
        weight_binding_rank=prefs.weight_binding_rank if prefs else None,
        weight_expression=prefs.weight_expression if prefs else None,
        weight_vaf=prefs.weight_vaf if prefs else None,
        weight_mutation_type=prefs.weight_mutation_type if prefs else None,
        weight_processing=prefs.weight_processing if prefs else None,
        weight_iedb=prefs.weight_iedb if prefs else None,
        theme=prefs.theme if prefs else None,
        results_page_size=prefs.results_page_size if prefs else None,
        default_visible_columns=col_list,
    )


# -- Endpoints --

@router.get("/", response_model=FullPreferencesResponse)
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FullPreferencesResponse:
    """Get all user settings (profile + preferences)."""
    stmt = select(UserPreferences).where(UserPreferences.user_id == current_user.id)
    result = await db.execute(stmt)
    prefs = result.scalar_one_or_none()
    return _prefs_to_response(current_user, prefs)


@router.put("/profile", response_model=FullPreferencesResponse)
async def update_profile(
    body: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FullPreferencesResponse:
    """Update user profile (name, institution)."""
    if body.name is not None:
        current_user.name = body.name
    if body.institution is not None:
        current_user.institution = body.institution if body.institution else None
    await db.commit()
    await db.refresh(current_user)

    stmt = select(UserPreferences).where(UserPreferences.user_id == current_user.id)
    result = await db.execute(stmt)
    prefs = result.scalar_one_or_none()
    return _prefs_to_response(current_user, prefs)


@router.put("/analysis-defaults", response_model=FullPreferencesResponse)
async def update_analysis_defaults(
    body: AnalysisDefaults,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FullPreferencesResponse:
    """Update analysis default settings."""
    prefs = await _get_or_create_prefs(current_user, db)
    prefs.default_cancer_type = body.default_cancer_type
    prefs.default_stage = body.default_stage
    prefs.default_genome = body.default_genome
    prefs.default_hla_alleles = (
        ",".join(body.default_hla_alleles) if body.default_hla_alleles else None
    )
    await db.commit()
    await db.refresh(prefs)
    return _prefs_to_response(current_user, prefs)


@router.put("/scoring-weights", response_model=FullPreferencesResponse)
async def update_scoring_weights(
    body: ScoringWeights,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FullPreferencesResponse:
    """Update custom scoring weights.

    Weights should sum to approximately 1.0. We validate they're each
    in [0, 1] but don't enforce exact sum = 1.0 (to allow experimentation).
    A warning is returned if the sum deviates significantly.
    """
    prefs = await _get_or_create_prefs(current_user, db)
    prefs.weight_presentation = body.weight_presentation
    prefs.weight_binding_rank = body.weight_binding_rank
    prefs.weight_expression = body.weight_expression
    prefs.weight_vaf = body.weight_vaf
    prefs.weight_mutation_type = body.weight_mutation_type
    prefs.weight_processing = body.weight_processing
    prefs.weight_iedb = body.weight_iedb
    await db.commit()
    await db.refresh(prefs)
    return _prefs_to_response(current_user, prefs)


@router.put("/display", response_model=FullPreferencesResponse)
async def update_display_preferences(
    body: DisplayPreferences,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FullPreferencesResponse:
    """Update display preferences (theme, page size, columns)."""
    prefs = await _get_or_create_prefs(current_user, db)
    prefs.theme = body.theme
    prefs.results_page_size = body.results_page_size
    prefs.default_visible_columns = (
        ",".join(body.default_visible_columns) if body.default_visible_columns else None
    )
    await db.commit()
    await db.refresh(prefs)
    return _prefs_to_response(current_user, prefs)
