"""
Vaccine construct builder API.

Takes a set of selected epitopes and assembles them into a polyepitope
vaccine construct. Provides ordering strategies, 25mer context extraction,
and proteasomal cleavage prediction via pepsickle.

The construct visualisation on the frontend follows the LV2i style:
scrollable heatmap bar with per-position scoring, gene region annotations,
linker sequences, and cleavage overlay.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Epitope, Variant, User, Analysis, Project
from app.routers.auth import get_current_user
from app.pipeline.peptide_gen import extract_25mer_context, _get_protein_sequence
from app.construct_utils import (
    confidence_tier as _confidence_tier,
    gene_color as _gene_color,
    order_by_immunogenicity as _order_by_immunogenicity,
    order_alternating_ends as _order_alternating_ends,
    order_gene_cluster as _order_gene_cluster,
    GENE_COLORS as _GENE_COLORS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/construct", tags=["construct"])


# -- Request / Response schemas --


class ConstructBuildRequest(BaseModel):
    analysis_id: int
    epitope_ids: list[int]
    ordering: str = Field(
        default="immunogenicity",
        pattern="^(immunogenicity|alternating|gene_cluster|manual)$",
    )
    sequence_mode: str = Field(
        default="epitope",
        pattern="^(epitope|25mer)$",
    )
    linker: str = Field(default="AAY", max_length=20)


class EpitopeInConstruct(BaseModel):
    id: int
    peptide_seq: str
    peptide_length: int
    gene: Optional[str]
    protein_change: Optional[str]
    variant_type: Optional[str]
    hla_allele: str
    binding_affinity_nm: float
    immunogenicity_score: float
    confidence_tier: str
    start_pos: int          # 0-based position in the construct
    end_pos: int            # exclusive end
    context_25mer: Optional[str]  # mutant 25mer if available
    wt_25mer: Optional[str]
    mutation_position_in_context: Optional[int]


class LinkerPosition(BaseModel):
    start: int
    end: int
    sequence: str


class Region(BaseModel):
    name: str
    start: int
    end: int       # exclusive
    color: str


class ConstructBuildResponse(BaseModel):
    construct_sequence: str
    total_length: int
    epitopes: list[EpitopeInConstruct]
    linker_positions: list[LinkerPosition]
    regions: list[Region]
    ordering_used: str
    warnings: list[str] = []  # non-fatal issues (e.g. 25mer lookup failures)


class CleavageRequest(BaseModel):
    sequence: str = Field(..., min_length=10, max_length=5000)
    epitope_boundaries: list[list[int]]    # [[start, end], ...]
    linker_positions: list[list[int]]      # [[start, end], ...]


class JunctionCleavage(BaseModel):
    junction_index: int
    position: int
    score: float
    is_correct_cleavage: bool  # True if cleavage predicted at intended boundary


class CleavageResponse(BaseModel):
    cleavage_scores: list[float]  # per-position cleavage probability
    junction_cleavage: list[JunctionCleavage]


# Confidence tier, gene colors, and ordering algorithms are imported from
# app.construct_utils to keep them testable without heavy deps.


# -- Endpoints --


@router.post("/build", response_model=ConstructBuildResponse)
async def build_construct(
    req: ConstructBuildRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConstructBuildResponse:
    """
    Assemble a polyepitope vaccine construct from selected epitopes.

    Fetches the selected epitopes, orders them per the chosen strategy,
    concatenates with linker sequences, extracts 25mer context where
    possible, and returns the full construct with position annotations.
    """
    if not req.epitope_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No epitope IDs provided",
        )

    if len(req.epitope_ids) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 50 epitopes per construct",
        )

    # Fetch epitopes with their variants
    stmt = (
        select(Epitope)
        .options(selectinload(Epitope.variant))
        .where(Epitope.id.in_(req.epitope_ids))
    )
    result = await db.execute(stmt)
    epitopes = list(result.scalars().all())

    if not epitopes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No epitopes found for the given IDs",
        )

    # Verify all epitopes belong to the same analysis and user owns it
    analysis_ids = set(e.analysis_id for e in epitopes)
    if len(analysis_ids) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All epitopes must belong to the same analysis",
        )

    # Ownership check: Analysis -> Project -> user_id
    ownership_stmt = (
        select(Analysis, Project)
        .join(Project, Analysis.project_id == Project.id)
        .where(Analysis.id == epitopes[0].analysis_id)
    )
    ownership_result = await db.execute(ownership_stmt)
    row = ownership_result.one_or_none()
    if not row or row[1].user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Order epitopes by requested strategy
    if req.ordering == "manual":
        # Preserve the order of epitope_ids as provided by the user
        id_order = {eid: i for i, eid in enumerate(req.epitope_ids)}
        epitopes.sort(key=lambda e: id_order.get(e.id, 999))
    elif req.ordering == "alternating":
        epitopes = _order_alternating_ends(epitopes)
    elif req.ordering == "gene_cluster":
        # Need gene info for clustering
        gene_map = {}
        for ep in epitopes:
            gene_map[ep.id] = ep.variant.gene if ep.variant else "unknown"
        from collections import defaultdict
        groups = defaultdict(list)
        for ep in epitopes:
            groups[gene_map[ep.id]].append(ep)
        epitopes = []
        sorted_groups = sorted(
            groups.items(),
            key=lambda g: max(e.immunogenicity_score for e in g[1]),
            reverse=True,
        )
        for _gene, group_eps in sorted_groups:
            group_eps.sort(key=lambda e: e.immunogenicity_score, reverse=True)
            epitopes.extend(group_eps)
    else:
        # Default: immunogenicity descending
        epitopes = _order_by_immunogenicity(epitopes)

    # 25mer context extraction. Only run when sequence_mode is "25mer" to
    # keep the default build fast. pyensembl lookups are expensive (download +
    # index on first call) and not needed for the epitope-only view.
    context_cache: dict[int, Optional[dict]] = {}
    build_warnings: list[str] = []

    if req.sequence_mode == "25mer":
        protein_cache: dict[str, Optional[str]] = {}
        success_count = 0
        fail_count = 0
        for ep in epitopes:
            try:
                variant = ep.variant
                if not variant or not variant.gene or not variant.protein_change:
                    context_cache[ep.id] = None
                    fail_count += 1
                    continue

                if variant.annotation_json and "context_25mer" in variant.annotation_json:
                    context_cache[ep.id] = variant.annotation_json["context_25mer"]
                    success_count += 1
                    continue

                gene = variant.gene
                if gene not in protein_cache:
                    try:
                        protein_cache[gene] = _get_protein_sequence(gene)
                    except Exception as e:
                        logger.warning(f"pyensembl lookup failed for {gene}: {e}")
                        protein_cache[gene] = None

                protein_seq = protein_cache.get(gene)
                if protein_seq:
                    ctx = extract_25mer_context(
                        protein_seq=protein_seq,
                        protein_change=variant.protein_change,
                        variant_type=variant.variant_type,
                    )
                    context_cache[ep.id] = ctx
                    if ctx:
                        success_count += 1
                    else:
                        fail_count += 1
                else:
                    context_cache[ep.id] = None
                    fail_count += 1
            except Exception as e:
                logger.warning(f"25mer extraction failed for epitope {ep.id}: {e}")
                context_cache[ep.id] = None
                fail_count += 1

        if fail_count > 0:
            if success_count == 0:
                build_warnings.append(
                    f"25mer extraction failed for all {fail_count} epitopes. "
                    "pyensembl data may not be installed. Falling back to minimal epitope sequences."
                )
            else:
                build_warnings.append(
                    f"25mer extraction: {success_count} succeeded, {fail_count} failed (missing gene/protein data). "
                    "Failed epitopes use minimal sequence."
                )

    # Build the construct sequence
    construct_parts = []
    epitope_responses = []
    linker_positions = []
    regions = []
    gene_color_map: dict[str, str] = {}
    current_pos = 0

    for i, ep in enumerate(epitopes):
        variant = ep.variant
        ctx = context_cache.get(ep.id)

        # Determine the sequence to use for this epitope in the construct
        if req.sequence_mode == "25mer" and ctx and ctx.get("mut_25mer"):
            seq = ctx["mut_25mer"]
        else:
            seq = ep.peptide_seq

        # Add linker between epitopes (not before the first one)
        if i > 0 and req.linker:
            linker_positions.append(LinkerPosition(
                start=current_pos,
                end=current_pos + len(req.linker),
                sequence=req.linker,
            ))
            construct_parts.append(req.linker)
            current_pos += len(req.linker)

        gene = variant.gene if variant else None
        start = current_pos
        end = current_pos + len(seq)

        # Gene region annotation
        regions.append(Region(
            name=gene or "unknown",
            start=start,
            end=end,
            color=_gene_color(gene, gene_color_map),
        ))

        epitope_responses.append(EpitopeInConstruct(
            id=ep.id,
            peptide_seq=ep.peptide_seq,
            peptide_length=ep.peptide_length,
            gene=gene,
            protein_change=variant.protein_change if variant else None,
            variant_type=variant.variant_type if variant else None,
            hla_allele=ep.hla_allele,
            binding_affinity_nm=ep.binding_affinity_nm,
            immunogenicity_score=ep.immunogenicity_score,
            confidence_tier=_confidence_tier(ep.immunogenicity_score, ep.binding_affinity_nm),
            start_pos=start,
            end_pos=end,
            context_25mer=ctx.get("mut_25mer") if ctx else None,
            wt_25mer=ctx.get("wt_25mer") if ctx else None,
            mutation_position_in_context=ctx.get("mutation_position") if ctx else None,
        ))

        construct_parts.append(seq)
        current_pos = end

    construct_sequence = "".join(construct_parts)

    return ConstructBuildResponse(
        construct_sequence=construct_sequence,
        total_length=len(construct_sequence),
        epitopes=epitope_responses,
        linker_positions=linker_positions,
        regions=regions,
        ordering_used=req.ordering,
        warnings=build_warnings,
    )


@router.post("/cleavage", response_model=CleavageResponse)
async def predict_cleavage(
    req: CleavageRequest,
    current_user: User = Depends(get_current_user),
) -> CleavageResponse:
    """
    Run pepsickle proteasomal cleavage prediction on a construct sequence.

    Returns per-position cleavage probabilities and highlights whether
    cleavage is predicted at the intended junction sites (between epitopes).
    Correct cleavage at junctions = good (the proteasome will liberate
    individual epitopes). Cleavage within epitopes = bad (destroys them).
    """
    try:
        import pepsickle
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Pepsickle is not installed. Install with: pip install pepsickle",
        )

    sequence = req.sequence.upper()

    # Validate sequence contains only standard amino acids
    valid_aa = set("ACDEFGHIKLMNPQRSTVWY")
    invalid = set(sequence) - valid_aa
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid amino acids in sequence: {', '.join(sorted(invalid))}",
        )

    try:
        model = pepsickle.initialize_epitope_model()
        result = pepsickle.predict_protein_cleavage_locations(
            protein_seq=sequence,
            model=model,
            protein_id="construct",
            mod_type="epitope",
            proteasome_type="C",  # constitutive proteasome
            threshold=0.5,
        )

        # pepsickle returns a list of tuples:
        # (position, residue, cleavage_prob, is_cleaved, protein_id)
        # NOT a pandas DataFrame despite what older docs suggest.
        cleavage_scores = [row[2] for row in result]

    except Exception as e:
        logger.error(f"Pepsickle prediction failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cleavage prediction failed: {str(e)}",
        )

    # Analyze junction cleavage
    junction_cleavage = []
    for i, (lp_start, lp_end) in enumerate(req.linker_positions):
        # Check cleavage score at the junction boundaries
        # Ideal: high cleavage right before the linker (C-terminal of preceding epitope)
        # and right after the linker (N-terminal of next epitope)
        if lp_start > 0 and lp_start < len(cleavage_scores):
            score = cleavage_scores[lp_start - 1]  # C-terminal of preceding epitope
            junction_cleavage.append(JunctionCleavage(
                junction_index=i,
                position=lp_start - 1,
                score=score,
                is_correct_cleavage=score >= 0.5,
            ))

        if lp_end < len(cleavage_scores):
            score = cleavage_scores[lp_end]  # N-terminal of next epitope
            junction_cleavage.append(JunctionCleavage(
                junction_index=i,
                position=lp_end,
                score=score,
                is_correct_cleavage=score >= 0.5,
            ))

    return CleavageResponse(
        cleavage_scores=cleavage_scores,
        junction_cleavage=junction_cleavage,
    )
