"""
Backend endpoints serving track data for the IGV.js genome browser.

Two main tracks:
1. Variants -- somatic mutations as point features (displayed as variant track)
2. Epitopes -- predicted peptides as colored range features

Both return JSON in a format IGV.js custom tracks can consume directly.
The frontend IGV component fetches these via its data source callback.
"""

import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from app.database import get_db
from app.models import Analysis, Variant, Epitope, Project, User
from app.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# -- Shared ownership check (same pattern as epitopes router) --

async def _verify_analysis_ownership(
    analysis_id: int, current_user: User, db: AsyncSession
) -> tuple[Analysis, Project]:
    """Returns (analysis, project) -- we need project for reference_genome."""
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
            detail="Analysis not found",
        )

    analysis, project = row
    if project.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this analysis",
        )

    return analysis, project


# -- Response models --

class VariantFeature(BaseModel):
    """One variant for IGV.js variant track."""
    chr: str
    start: int
    end: int
    name: str        # e.g. "BRAF V600E"
    ref: str
    alt: str
    gene: Optional[str]
    variant_type: str
    vaf: Optional[float]
    variant_id: int


class EpitopeFeature(BaseModel):
    """One epitope for IGV.js annotation track.
    Rendered as a colored bar spanning the mutation position."""
    chr: str
    start: int
    end: int
    name: str         # peptide sequence
    score: float      # immunogenicity_score 0-1
    hla_allele: str
    rank: int
    tier: str         # high/medium/low -- drives color
    binding_affinity_nm: float
    gene: Optional[str]
    protein_change: Optional[str]
    epitope_id: int


class BrowserTracksResponse(BaseModel):
    """All track data needed for the genome browser."""
    reference_genome: str  # GRCh38 or GRCh37
    variants: List[VariantFeature]
    epitopes: List[EpitopeFeature]


def _confidence_tier(score: float, affinity_nm: float) -> str:
    if score >= 0.7 and affinity_nm <= 50:
        return "high"
    if score >= 0.4 and affinity_nm <= 500:
        return "medium"
    return "low"


@router.get("/{analysis_id}/browser/tracks", response_model=BrowserTracksResponse)
async def get_browser_tracks(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BrowserTracksResponse:
    """
    Return all track data for one analysis in a single request.

    One combined endpoint rather than separate variant/epitope endpoints
    because the browser needs both at once and the data is small
    (typically <100 variants, <1000 epitopes after pipeline filtering).
    """
    analysis, project = await _verify_analysis_ownership(analysis_id, current_user, db)

    # Fetch all variants for this analysis
    variant_stmt = (
        select(Variant)
        .where(Variant.analysis_id == analysis_id)
        .order_by(Variant.chrom, Variant.pos)
    )
    variants_result = await db.execute(variant_stmt)
    variants = variants_result.scalars().all()

    # Fetch all epitopes with their variants
    epitope_stmt = (
        select(Epitope)
        .where(Epitope.analysis_id == analysis_id)
        .options(selectinload(Epitope.variant))
        .order_by(Epitope.rank)
    )
    epitopes_result = await db.execute(epitope_stmt)
    epitopes = epitopes_result.scalars().all()

    # Build variant features
    # VCF positions are 1-based; IGV.js features use 0-based half-open coords
    variant_features = []
    for v in variants:
        start_0 = v.pos - 1  # convert 1-based to 0-based
        end_0 = start_0 + len(v.ref)
        label = f"{v.gene} {v.protein_change}" if v.gene and v.protein_change else v.gene or f"{v.chrom}:{v.pos}"
        variant_features.append(VariantFeature(
            chr=v.chrom,
            start=start_0,
            end=end_0,
            name=label,
            ref=v.ref,
            alt=v.alt,
            gene=v.gene,
            variant_type=v.variant_type,
            vaf=v.vaf,
            variant_id=v.id,
        ))

    # Build epitope features
    # Epitopes don't have their own genomic coords -- they map to their variant's position.
    # We display them as short spans at the variant locus, offset vertically by IGV.
    epitope_features = []
    for ep in epitopes:
        v = ep.variant
        if not v:
            continue
        # Span: variant 0-based start to + peptide CDS footprint (approximate)
        # This is a display heuristic -- the real CDS span depends on reading frame
        start_0 = v.pos - 1  # 1-based to 0-based
        cds_span = ep.peptide_length * 3
        tier = _confidence_tier(ep.immunogenicity_score, ep.binding_affinity_nm)
        epitope_features.append(EpitopeFeature(
            chr=v.chrom,
            start=start_0,
            end=start_0 + cds_span,
            name=ep.peptide_seq,
            score=ep.immunogenicity_score,
            hla_allele=ep.hla_allele,
            rank=ep.rank,
            tier=tier,
            binding_affinity_nm=ep.binding_affinity_nm,
            gene=v.gene,
            protein_change=v.protein_change,
            epitope_id=ep.id,
        ))

    return BrowserTracksResponse(
        reference_genome=project.reference_genome,
        variants=variant_features,
        epitopes=epitope_features,
    )
