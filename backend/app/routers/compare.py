"""
Multi-patient comparison API.

Compares neoantigen predictions across multiple analyses to find:
- Shared mutations (same gene + protein_change across patients)
- Shared peptides (identical neoepitope sequences)
- Per-gene neoantigen frequency heatmap data

Useful for identifying public neoantigens for off-the-shelf vaccines
vs private neoantigens for personalized approaches.
"""
import logging
from typing import Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Epitope, Variant, Analysis, Project, User
from app.routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/compare", tags=["compare"])


class CompareRequest(BaseModel):
    analysis_ids: list[int] = Field(..., min_length=2, max_length=20)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    max_ic50: float = Field(default=500.0, ge=0.0)


class GeneHeatmapCell(BaseModel):
    """One cell in the gene x analysis heatmap."""
    gene: str
    analysis_id: int
    epitope_count: int
    best_score: float
    best_peptide: str
    best_hla: str
    best_ic50: float


class SharedPeptide(BaseModel):
    """A peptide sequence found in multiple analyses."""
    peptide_seq: str
    gene: Optional[str]
    protein_change: Optional[str]
    hla_allele: str
    analysis_ids: list[int]
    scores: list[float]  # one per analysis_id, same order
    affinities: list[float]


class SharedMutation(BaseModel):
    """A mutation (gene + protein_change) found in multiple analyses."""
    gene: str
    protein_change: str
    analysis_ids: list[int]
    count: int


class AnalysisSummary(BaseModel):
    analysis_id: int
    project_name: str
    cancer_type: str
    total_epitopes: int
    total_genes: int


class CompareResponse(BaseModel):
    analyses: list[AnalysisSummary]
    genes: list[str]  # all genes across analyses, sorted
    heatmap: list[GeneHeatmapCell]
    shared_peptides: list[SharedPeptide]
    shared_mutations: list[SharedMutation]


@router.post("/analyses", response_model=CompareResponse)
async def compare_analyses(
    req: CompareRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompareResponse:
    """
    Compare neoantigen predictions across multiple analyses.

    Returns a gene-level heatmap and lists of shared peptides/mutations.
    All analyses must belong to the current user.
    """
    # Verify ownership of all analyses
    stmt = (
        select(Analysis, Project)
        .join(Project, Analysis.project_id == Project.id)
        .where(Analysis.id.in_(req.analysis_ids))
    )
    result = await db.execute(stmt)
    rows = result.all()

    if len(rows) != len(req.analysis_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more analyses not found",
        )

    for _, project in rows:
        if project.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to one or more analyses",
            )

    # Build analysis metadata
    analysis_map = {a.id: (a, p) for a, p in rows}

    # Fetch all epitopes for the requested analyses
    ep_stmt = (
        select(Epitope)
        .options(selectinload(Epitope.variant))
        .where(
            Epitope.analysis_id.in_(req.analysis_ids),
            Epitope.immunogenicity_score >= req.min_score,
            Epitope.binding_affinity_nm <= req.max_ic50,
        )
    )
    ep_result = await db.execute(ep_stmt)
    epitopes = list(ep_result.scalars().all())

    # Build gene x analysis heatmap
    # Key: (gene, analysis_id) -> list of epitopes
    gene_analysis: dict[tuple[str, int], list] = defaultdict(list)
    all_genes: set[str] = set()

    # Track peptide occurrences across analyses
    # Key: (peptide_seq, hla_allele) -> {analysis_id: epitope}
    peptide_map: dict[tuple[str, str], dict[int, Epitope]] = defaultdict(dict)

    # Track mutation occurrences
    # Key: (gene, protein_change) -> set of analysis_ids
    mutation_map: dict[tuple[str, str], set[int]] = defaultdict(set)

    # Per-analysis stats
    analysis_genes: dict[int, set[str]] = defaultdict(set)
    analysis_count: dict[int, int] = defaultdict(int)

    for ep in epitopes:
        v = ep.variant
        gene = v.gene if v else "unknown"
        all_genes.add(gene)
        gene_analysis[(gene, ep.analysis_id)].append(ep)
        analysis_genes[ep.analysis_id].add(gene)
        analysis_count[ep.analysis_id] += 1

        peptide_map[(ep.peptide_seq, ep.hla_allele)][ep.analysis_id] = ep

        if v and v.protein_change:
            mutation_map[(gene, v.protein_change)].add(ep.analysis_id)

    # Build heatmap cells
    sorted_genes = sorted(all_genes)
    heatmap: list[GeneHeatmapCell] = []
    for gene in sorted_genes:
        for aid in req.analysis_ids:
            eps = gene_analysis.get((gene, aid), [])
            if eps:
                best = max(eps, key=lambda e: e.immunogenicity_score)
                heatmap.append(GeneHeatmapCell(
                    gene=gene,
                    analysis_id=aid,
                    epitope_count=len(eps),
                    best_score=best.immunogenicity_score,
                    best_peptide=best.peptide_seq,
                    best_hla=best.hla_allele,
                    best_ic50=best.binding_affinity_nm,
                ))

    # Find shared peptides (same sequence + same HLA in >1 analysis)
    shared_peptides: list[SharedPeptide] = []
    for (pep_seq, hla), analysis_eps in peptide_map.items():
        if len(analysis_eps) >= 2:
            # Get gene/mutation from any one
            any_ep = next(iter(analysis_eps.values()))
            v = any_ep.variant
            aids = sorted(analysis_eps.keys())
            shared_peptides.append(SharedPeptide(
                peptide_seq=pep_seq,
                gene=v.gene if v else None,
                protein_change=v.protein_change if v else None,
                hla_allele=hla,
                analysis_ids=aids,
                scores=[analysis_eps[a].immunogenicity_score for a in aids],
                affinities=[analysis_eps[a].binding_affinity_nm for a in aids],
            ))

    shared_peptides.sort(key=lambda s: len(s.analysis_ids), reverse=True)

    # Find shared mutations
    shared_mutations: list[SharedMutation] = []
    for (gene, pchange), aids in mutation_map.items():
        if len(aids) >= 2:
            shared_mutations.append(SharedMutation(
                gene=gene,
                protein_change=pchange,
                analysis_ids=sorted(aids),
                count=len(aids),
            ))
    shared_mutations.sort(key=lambda m: m.count, reverse=True)

    # Build analysis summaries
    analyses_summary = []
    for aid in req.analysis_ids:
        analysis, project = analysis_map[aid]
        analyses_summary.append(AnalysisSummary(
            analysis_id=aid,
            project_name=project.name,
            cancer_type=project.cancer_type,
            total_epitopes=analysis_count.get(aid, 0),
            total_genes=len(analysis_genes.get(aid, set())),
        ))

    return CompareResponse(
        analyses=analyses_summary,
        genes=sorted_genes,
        heatmap=heatmap,
        shared_peptides=shared_peptides[:100],  # cap at 100 to avoid huge responses
        shared_mutations=shared_mutations[:100],
    )
