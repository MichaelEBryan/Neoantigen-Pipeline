import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import desc, asc, func
from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from app.database import get_db
from app.models import Epitope, Analysis, Variant, Project, User
from app.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _confidence_tier(score: float, affinity_nm: float) -> str:
    """
    Assign confidence tier based on immunogenicity score and binding affinity.

    High: score >= 0.7 AND IC50 <= 50 nM (strong binder + high composite)
    Medium: score >= 0.4 AND IC50 <= 500 nM
    Low: everything else that passed the pipeline filter
    """
    if score >= 0.7 and affinity_nm <= 50:
        return "high"
    if score >= 0.4 and affinity_nm <= 500:
        return "medium"
    return "low"


class EpitopeResponse(BaseModel):
    """Response model for epitope with all 12 PLAN.md columns + extras."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    analysis_id: int
    variant_id: int
    peptide_seq: str
    peptide_length: int
    rank: int
    gene: Optional[str]
    chrom: Optional[str]
    pos: Optional[int]
    protein_change: Optional[str]
    variant_type: Optional[str]
    hla_allele: str
    binding_affinity_nm: float
    presentation_score: float
    processing_score: Optional[float]
    expression_tpm: Optional[float]
    immunogenicity_score: float
    dai_score: Optional[float]  # Differential Agretopicity Index
    wt_binding_affinity_nm: Optional[float]  # WT IC50 for DAI context
    confidence_tier: str  # high / medium / low
    explanation_json: Optional[Dict[str, Any]]


class EpitopeListResponse(BaseModel):
    """Paginated list of epitopes with total count."""
    epitopes: List[EpitopeResponse]
    total: int
    skip: int
    limit: int


def _build_epitope_response(epitope: Epitope, variant: Variant) -> EpitopeResponse:
    """Build EpitopeResponse from ORM objects."""
    return EpitopeResponse(
        id=epitope.id,
        analysis_id=epitope.analysis_id,
        variant_id=epitope.variant_id,
        peptide_seq=epitope.peptide_seq,
        peptide_length=epitope.peptide_length,
        rank=epitope.rank,
        gene=variant.gene if variant else None,
        chrom=variant.chrom if variant else None,
        pos=variant.pos if variant else None,
        protein_change=variant.protein_change if variant else None,
        variant_type=variant.variant_type if variant else None,
        hla_allele=epitope.hla_allele,
        binding_affinity_nm=epitope.binding_affinity_nm,
        presentation_score=epitope.presentation_score,
        processing_score=epitope.processing_score,
        expression_tpm=epitope.expression_tpm,
        immunogenicity_score=epitope.immunogenicity_score,
        dai_score=epitope.dai_score,
        wt_binding_affinity_nm=epitope.wt_binding_affinity_nm,
        confidence_tier=_confidence_tier(
            epitope.immunogenicity_score, epitope.binding_affinity_nm
        ),
        explanation_json=epitope.explanation_json,
    )


async def _verify_analysis_ownership(
    analysis_id: int, current_user: User, db: AsyncSession
) -> Analysis:
    """Check analysis exists and belongs to current user. Single query."""
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


def _build_epitope_query(
    analysis_id: int,
    gene: Optional[str],
    variant_type: Optional[str],
    hla_allele: Optional[str],
    min_score: Optional[float],
    confidence_tier: Optional[str],
    deduplicate: bool = False,
):
    """
    Build the filtered query. Shared by list and export endpoints.
    If deduplicate=True, keep only the highest-scoring epitope per variant.
    """
    stmt = select(Epitope).join(Variant).where(Epitope.analysis_id == analysis_id)

    if gene:
        stmt = stmt.where(Variant.gene == gene)
    if variant_type:
        stmt = stmt.where(Variant.variant_type == variant_type)
    if hla_allele:
        stmt = stmt.where(Epitope.hla_allele == hla_allele)
    if min_score is not None:
        stmt = stmt.where(Epitope.immunogenicity_score >= min_score)

    # Confidence tier is computed, not stored. We approximate with DB-level
    # thresholds matching _confidence_tier() logic.
    if confidence_tier == "high":
        stmt = stmt.where(
            Epitope.immunogenicity_score >= 0.7,
            Epitope.binding_affinity_nm <= 50,
        )
    elif confidence_tier == "medium":
        # Medium but NOT high: score >= 0.4 AND affinity <= 500,
        # excluding high (score >= 0.7 AND affinity <= 50)
        stmt = stmt.where(
            Epitope.immunogenicity_score >= 0.4,
            Epitope.binding_affinity_nm <= 500,
        )
        stmt = stmt.where(
            (Epitope.immunogenicity_score < 0.7)
            | (Epitope.binding_affinity_nm > 50)
        )
    elif confidence_tier == "low":
        # Low = not high and not medium = NOT(score >= 0.4 AND affinity <= 500)
        stmt = stmt.where(
            (Epitope.immunogenicity_score < 0.4)
            | (Epitope.binding_affinity_nm > 500)
        )

    if deduplicate:
        # Keep only the best-scoring epitope per variant. Uses a subquery
        # to find the max immunogenicity_score per variant_id, then filters.
        best_per_variant = (
            select(
                Epitope.variant_id,
                func.max(Epitope.immunogenicity_score).label("max_score"),
            )
            .where(Epitope.analysis_id == analysis_id)
            .group_by(Epitope.variant_id)
            .subquery()
        )
        stmt = stmt.where(
            Epitope.variant_id == best_per_variant.c.variant_id,
            Epitope.immunogenicity_score == best_per_variant.c.max_score,
        )

    return stmt


class FilterOptionsResponse(BaseModel):
    """Unique values available for each filter dropdown."""
    genes: List[str]
    variant_types: List[str]
    hla_alleles: List[str]


@router.get("/{analysis_id}/epitopes/filter-options", response_model=FilterOptionsResponse)
async def get_filter_options(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FilterOptionsResponse:
    """
    Return distinct filter values for all epitopes in an analysis.
    Called once on page load to populate filter dropdowns correctly
    (not just from the current page of results).
    """
    await _verify_analysis_ownership(analysis_id, current_user, db)

    # Three lightweight queries for distinct values
    genes_q = (
        select(Variant.gene)
        .join(Epitope, Epitope.variant_id == Variant.id)
        .where(Epitope.analysis_id == analysis_id, Variant.gene.isnot(None))
        .distinct()
    )
    types_q = (
        select(Variant.variant_type)
        .join(Epitope, Epitope.variant_id == Variant.id)
        .where(Epitope.analysis_id == analysis_id, Variant.variant_type.isnot(None))
        .distinct()
    )
    hla_q = (
        select(Epitope.hla_allele)
        .where(Epitope.analysis_id == analysis_id)
        .distinct()
    )

    genes_res, types_res, hla_res = await db.execute(genes_q), await db.execute(types_q), await db.execute(hla_q)

    return FilterOptionsResponse(
        genes=sorted(genes_res.scalars().all()),
        variant_types=sorted(types_res.scalars().all()),
        hla_alleles=sorted(hla_res.scalars().all()),
    )


@router.get("/{analysis_id}/epitopes", response_model=EpitopeListResponse)
async def list_epitopes(
    analysis_id: int,
    sort_by: str = Query("rank", pattern="^(rank|immunogenicity_score|binding_affinity_nm|presentation_score|gene|peptide_length)$"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    gene: Optional[str] = Query(None),
    variant_type: Optional[str] = Query(None),
    hla_allele: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    confidence_tier: Optional[str] = Query(None, pattern="^(high|medium|low)$"),
    deduplicate: bool = Query(False, description="If true, show only the best-scoring epitope per mutation"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EpitopeListResponse:
    """
    List epitopes for an analysis with sorting, filtering, and pagination.
    Returns total count for pagination UI.
    When deduplicate=true, shows only the highest-scoring epitope per variant
    (collapses overlapping 8-11mers from the same mutation).
    """
    await _verify_analysis_ownership(analysis_id, current_user, db)

    base_stmt = _build_epitope_query(
        analysis_id, gene, variant_type, hla_allele, min_score, confidence_tier,
        deduplicate=deduplicate,
    )

    # Total count (for pagination)
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Sorting
    sort_columns = {
        "rank": Epitope.rank,
        "immunogenicity_score": Epitope.immunogenicity_score,
        "binding_affinity_nm": Epitope.binding_affinity_nm,
        "presentation_score": Epitope.presentation_score,
        "gene": Variant.gene,
        "peptide_length": Epitope.peptide_length,
    }
    sort_col = sort_columns[sort_by]
    base_stmt = base_stmt.order_by(
        desc(sort_col) if sort_order == "desc" else asc(sort_col)
    )

    # Eager load variant, paginate
    base_stmt = base_stmt.options(selectinload(Epitope.variant))
    base_stmt = base_stmt.offset(skip).limit(limit)

    result = await db.execute(base_stmt)
    epitopes = result.scalars().all()

    return EpitopeListResponse(
        epitopes=[_build_epitope_response(ep, ep.variant) for ep in epitopes],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{analysis_id}/epitopes/export")
async def export_epitopes_csv(
    analysis_id: int,
    format: str = Query("csv", pattern="^(csv|tsv)$"),
    gene: Optional[str] = Query(None),
    variant_type: Optional[str] = Query(None),
    hla_allele: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    confidence_tier: Optional[str] = Query(None, pattern="^(high|medium|low)$"),
    deduplicate: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Export epitopes as CSV or TSV. Applies same filters as list endpoint.
    No pagination -- exports all matching rows.
    """
    await _verify_analysis_ownership(analysis_id, current_user, db)

    stmt = _build_epitope_query(
        analysis_id, gene, variant_type, hla_allele, min_score, confidence_tier,
        deduplicate=deduplicate,
    )
    stmt = stmt.order_by(asc(Epitope.rank))
    stmt = stmt.options(selectinload(Epitope.variant))

    result = await db.execute(stmt)
    epitopes = result.scalars().all()

    # Build CSV/TSV in memory
    delimiter = "\t" if format == "tsv" else ","
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter)

    # Header
    writer.writerow([
        "rank", "peptide_seq", "peptide_length", "gene", "protein_change",
        "genomic_position", "variant_type", "hla_allele",
        "binding_affinity_nm", "wt_binding_affinity_nm", "dai_score",
        "presentation_score", "processing_score",
        "expression_tpm", "immunogenicity_score", "confidence_tier",
    ])

    # Rows
    for ep in epitopes:
        v = ep.variant
        tier = _confidence_tier(ep.immunogenicity_score, ep.binding_affinity_nm)
        genomic_pos = f"{v.chrom}:{v.pos}" if v else ""
        writer.writerow([
            ep.rank,
            ep.peptide_seq,
            ep.peptide_length,
            v.gene if v else "",
            v.protein_change if v else "",
            genomic_pos,
            v.variant_type if v else "",
            ep.hla_allele,
            f"{ep.binding_affinity_nm:.1f}",
            f"{ep.wt_binding_affinity_nm:.1f}" if ep.wt_binding_affinity_nm is not None else "",
            f"{ep.dai_score:.4f}" if ep.dai_score is not None else "",
            f"{ep.presentation_score:.4f}",
            f"{ep.processing_score:.4f}" if ep.processing_score is not None else "",
            f"{ep.expression_tpm:.2f}" if ep.expression_tpm is not None else "",
            f"{ep.immunogenicity_score:.4f}",
            tier,
        ])

    content = output.getvalue()
    ext = format
    media_type = "text/tab-separated-values" if format == "tsv" else "text/csv"

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=epitopes_analysis_{analysis_id}.{ext}"
        },
    )


class EpitopeDetailResponse(BaseModel):
    """Extended detail response for explainability panel.
    Includes the base epitope data plus scorer weights and component breakdown."""
    model_config = ConfigDict(from_attributes=True)

    # All fields from EpitopeResponse
    id: int
    analysis_id: int
    variant_id: int
    peptide_seq: str
    peptide_length: int
    rank: int
    gene: Optional[str]
    chrom: Optional[str]
    pos: Optional[int]
    protein_change: Optional[str]
    variant_type: Optional[str]
    hla_allele: str
    binding_affinity_nm: float
    presentation_score: float
    processing_score: Optional[float]
    expression_tpm: Optional[float]
    immunogenicity_score: float
    dai_score: Optional[float]
    wt_binding_affinity_nm: Optional[float]
    confidence_tier: str
    explanation_json: Optional[Dict[str, Any]]
    vaf: Optional[float]

    # Scorer weights so the frontend can render the waterfall correctly
    # without hardcoding them (they might be tuned later)
    scorer_weights: Dict[str, float]

    # Neighbouring epitopes from same variant (for context)
    sibling_epitopes: List[Dict[str, Any]]


# Scorer weights -- imported once, exposed to frontend via the detail endpoint.
# This avoids the frontend hardcoding values that could drift.
from app.pipeline.scorer import WEIGHTS as SCORER_WEIGHTS


@router.get("/{epitope_id}", response_model=EpitopeDetailResponse)
async def get_epitope(
    epitope_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EpitopeDetailResponse:
    """Get detailed information about a single epitope, including full
    explainability data for the SHAP-style waterfall chart."""
    stmt = (
        select(Epitope)
        .options(selectinload(Epitope.variant))
        .where(Epitope.id == epitope_id)
    )
    result = await db.execute(stmt)
    epitope = result.scalar_one_or_none()

    if not epitope:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Epitope not found"
        )

    await _verify_analysis_ownership(epitope.analysis_id, current_user, db)

    variant = epitope.variant

    # Fetch sibling epitopes from the same variant (different HLA alleles or lengths)
    sibling_stmt = (
        select(Epitope)
        .where(
            Epitope.variant_id == epitope.variant_id,
            Epitope.id != epitope.id,
        )
        .order_by(asc(Epitope.rank))
        .limit(10)
    )
    sibling_result = await db.execute(sibling_stmt)
    siblings = [
        {
            "id": s.id,
            "peptide_seq": s.peptide_seq,
            "hla_allele": s.hla_allele,
            "rank": s.rank,
            "immunogenicity_score": s.immunogenicity_score,
            "binding_affinity_nm": s.binding_affinity_nm,
        }
        for s in sibling_result.scalars().all()
    ]

    return EpitopeDetailResponse(
        id=epitope.id,
        analysis_id=epitope.analysis_id,
        variant_id=epitope.variant_id,
        peptide_seq=epitope.peptide_seq,
        peptide_length=epitope.peptide_length,
        rank=epitope.rank,
        gene=variant.gene if variant else None,
        chrom=variant.chrom if variant else None,
        pos=variant.pos if variant else None,
        protein_change=variant.protein_change if variant else None,
        variant_type=variant.variant_type if variant else None,
        hla_allele=epitope.hla_allele,
        binding_affinity_nm=epitope.binding_affinity_nm,
        presentation_score=epitope.presentation_score,
        processing_score=epitope.processing_score,
        expression_tpm=epitope.expression_tpm,
        immunogenicity_score=epitope.immunogenicity_score,
        dai_score=epitope.dai_score,
        wt_binding_affinity_nm=epitope.wt_binding_affinity_nm,
        confidence_tier=_confidence_tier(
            epitope.immunogenicity_score, epitope.binding_affinity_nm
        ),
        explanation_json=epitope.explanation_json,
        vaf=variant.vaf if variant else None,
        scorer_weights=SCORER_WEIGHTS,
        sibling_epitopes=siblings,
    )
