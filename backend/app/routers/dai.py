"""
DAI (Differential Agretopicity Index) computation endpoint.

Computes DAI on-demand for existing analyses where it wasn't calculated
during the original pipeline run. For each epitope, derives the wildtype
peptide and runs MHCflurry to compare mutant vs WT binding.

DAI = log2(WT_IC50 / mutant_IC50)
  - Positive: mutant binds MHC better than WT (good neoantigen)
  - Negative: WT binds better (less likely to be immunogenic)
  - None: couldn't compute (frameshift, missing data)
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Epitope, Variant, Analysis, Project, User
from app.routers.auth import get_current_user
from app.pipeline.scorer import derive_wt_peptide
from app.pipeline.mhc_predict import get_predictor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dai", tags=["dai"])


class DAIResult(BaseModel):
    epitope_id: int
    peptide_seq: str
    hla_allele: str
    mutant_ic50: float
    wt_ic50: Optional[float]
    wt_peptide: Optional[str]
    dai_score: Optional[float]


class DAIResponse(BaseModel):
    analysis_id: int
    total_epitopes: int
    computed: int
    skipped: int  # frameshifts or missing data
    results: list[DAIResult]


@router.post("/compute/{analysis_id}", response_model=DAIResponse)
async def compute_dai_for_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DAIResponse:
    """
    Compute DAI for all epitopes in an analysis.

    Runs MHCflurry on derived wildtype peptides and updates the DB.
    Idempotent: re-running overwrites previous DAI values.
    """
    import math

    # Ownership check
    ownership_stmt = (
        select(Analysis, Project)
        .join(Project, Analysis.project_id == Project.id)
        .where(Analysis.id == analysis_id)
    )
    row = (await db.execute(ownership_stmt)).one_or_none()
    if not row or row[1].user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Fetch all epitopes with variants
    stmt = (
        select(Epitope)
        .options(selectinload(Epitope.variant))
        .where(Epitope.analysis_id == analysis_id)
        .order_by(Epitope.rank)
    )
    result = await db.execute(stmt)
    epitopes = list(result.scalars().all())

    if not epitopes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No epitopes found for this analysis",
        )

    # Collect WT peptides to predict
    import re
    from app.pipeline.peptide_gen import _to_single_letter

    wt_requests: list[dict] = []  # {idx, wt_peptide, allele}
    skipped = 0

    for i, ep in enumerate(epitopes):
        v = ep.variant
        if not v or v.variant_type == "frameshift":
            skipped += 1
            continue

        # Parse protein_change for ref/alt AA
        ref_aa = None
        alt_aa = None
        if v.protein_change:
            m = re.match(r"p\.([A-Z][a-z]{0,2})(\d+)([A-Z][a-z]{0,2})", v.protein_change)
            if m:
                ref_aa = _to_single_letter(m.group(1))
                alt_aa = _to_single_letter(m.group(3))

        if not ref_aa or not alt_aa:
            skipped += 1
            continue

        # Get mutation position from explanation_json if available
        mut_pos = None
        if ep.explanation_json and "mutation_position_in_peptide" in ep.explanation_json:
            mut_pos = ep.explanation_json["mutation_position_in_peptide"]

        # If mutation_position not stored, try to find alt_aa in peptide
        if mut_pos is None:
            pos = ep.peptide_seq.find(alt_aa)
            if pos == -1:
                skipped += 1
                continue
            mut_pos = pos

        wt_pep = derive_wt_peptide(ep.peptide_seq, mut_pos, ref_aa, alt_aa)
        if not wt_pep or wt_pep == ep.peptide_seq:
            skipped += 1
            continue

        wt_requests.append({
            "idx": i,
            "wt_peptide": wt_pep,
            "allele": ep.hla_allele,
        })

    # Run MHCflurry on WT peptides
    predictor = get_predictor(use_mock=False)

    # Group by allele for efficient prediction
    allele_groups: dict[str, list[str]] = {}
    for req in wt_requests:
        allele = req["allele"]
        if allele not in allele_groups:
            allele_groups[allele] = []
        pep = req["wt_peptide"]
        if pep not in allele_groups[allele]:
            allele_groups[allele].append(pep)

    # Predict
    wt_ic50_map: dict[tuple[str, str], float] = {}
    for allele, peps in allele_groups.items():
        try:
            preds = predictor.predict(peps, [allele])
            for pred in preds:
                wt_ic50_map[(pred.peptide_seq, pred.hla_allele)] = pred.binding_affinity_nm
        except Exception as e:
            logger.warning(f"WT prediction failed for allele {allele}: {e}")

    # Compute DAI and update DB
    results: list[DAIResult] = []
    computed = 0

    for req in wt_requests:
        ep = epitopes[req["idx"]]
        wt_pep = req["wt_peptide"]
        wt_ic50 = wt_ic50_map.get((wt_pep, ep.hla_allele))

        if wt_ic50 and wt_ic50 > 0 and ep.binding_affinity_nm > 0:
            dai = round(math.log2(wt_ic50 / ep.binding_affinity_nm), 4)
            ep.dai_score = dai
            ep.wt_binding_affinity_nm = wt_ic50
            # Update explanation_json too
            if ep.explanation_json:
                ep.explanation_json = {
                    **ep.explanation_json,
                    "dai_score": dai,
                    "wt_binding_affinity_nm": wt_ic50,
                    "wt_peptide": wt_pep,
                }
            computed += 1
        else:
            dai = None
            wt_ic50 = None

        results.append(DAIResult(
            epitope_id=ep.id,
            peptide_seq=ep.peptide_seq,
            hla_allele=ep.hla_allele,
            mutant_ic50=ep.binding_affinity_nm,
            wt_ic50=wt_ic50,
            wt_peptide=wt_pep,
            dai_score=dai,
        ))

    await db.commit()

    logger.info(
        f"DAI computed for analysis {analysis_id}: "
        f"{computed}/{len(epitopes)} epitopes, {skipped} skipped"
    )

    return DAIResponse(
        analysis_id=analysis_id,
        total_epitopes=len(epitopes),
        computed=computed,
        skipped=skipped,
        results=results,
    )
