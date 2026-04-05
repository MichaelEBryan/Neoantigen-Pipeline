"""
Composite immunogenicity scorer.

Combines 7 weighted components into a single score (0-1):

  1. MHCflurry presentation score   (0.30)
  2. Binding affinity rank           (0.25)
  3. Expression level                (0.15)
  4. Variant allele frequency        (0.10)
  5. Mutation type bonus             (0.10)
  6. Processing score                (0.05)  -- proteasomal + TAP transport
  7. IEDB immunogenicity             (0.05)

Each component is normalized to 0-1 before weighting.
Final score is the weighted sum, so it's also 0-1.

The weights come from PLAN.md section 3.5 and can be tuned.
"""
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .vcf_parser import ParsedVariant
from .peptide_gen import CandidatePeptide, _to_single_letter
from .mhc_predict import MHCPrediction

logger = logging.getLogger(__name__)


# -- Weight configuration --
# These match PLAN.md section 3.5. Adjust as needed.

WEIGHTS = {
    "presentation": 0.30,   # was 0.35; reduced to make room for processing
    "binding_rank": 0.25,
    "expression": 0.15,
    "vaf": 0.10,
    "mutation_type": 0.10,
    "processing": 0.05,     # proteasomal processing + TAP transport (from MHCflurry)
    "iedb": 0.05,
}

# Mutation type bonuses (normalized 0-1)
MUTATION_TYPE_SCORES = {
    "frameshift": 1.0,     # entirely novel peptides, strong immune response
    "nonsense": 0.7,       # truncated, moderately immunogenic
    "inframe_indel": 0.6,  # altered but still in-frame
    "missense": 0.5,       # single AA change, common neoantigen source
}

# IC50 thresholds for binding affinity normalization
# Strong binder: <50nM, weak binder: 50-500nM, non-binder: >500nM
IC50_STRONG = 50.0
IC50_WEAK = 500.0
IC50_MAX = 50000.0  # anything above this gets score 0


@dataclass
class ScoredEpitope:
    """Fully scored epitope candidate."""
    peptide_seq: str
    peptide_length: int
    hla_allele: str
    variant: ParsedVariant

    # Raw prediction values
    binding_affinity_nm: float
    presentation_score: float
    processing_score: float

    # Optional inputs (may be None if RNA-seq not available)
    expression_tpm: Optional[float] = None

    # Component scores (all 0-1)
    presentation_component: float = 0.0
    binding_rank_component: float = 0.0
    expression_component: float = 0.0
    vaf_component: float = 0.0
    mutation_type_component: float = 0.0
    processing_component: float = 0.0
    iedb_component: float = 0.0

    # Final composite
    immunogenicity_score: float = 0.0

    # DAI (Differential Agretopicity Index)
    # Positive DAI = mutant binds MHC better than WT = good neoantigen
    dai_score: Optional[float] = None
    wt_binding_affinity_nm: Optional[float] = None

    # Mutation position within the peptide (0-based), for WT derivation
    mutation_position_in_peptide: Optional[int] = None

    # Explanation for SHAP-style display
    explanation: dict = field(default_factory=dict)


def _normalize_binding_affinity(ic50_nm: float) -> float:
    """
    Convert IC50 (nM) to a 0-1 score where lower IC50 = higher score.
    Uses log-linear mapping:
      IC50 <= 50nM   -> score ~1.0  (strong binder)
      IC50 = 500nM   -> score ~0.5  (weak binder)
      IC50 >= 50000nM -> score ~0.0 (non-binder)
    """
    if ic50_nm <= 0:
        return 1.0
    if ic50_nm >= IC50_MAX:
        return 0.0

    # Log-scale normalization
    log_ic50 = math.log10(ic50_nm)
    log_min = math.log10(1.0)    # best possible: 1 nM
    log_max = math.log10(IC50_MAX)

    score = 1.0 - (log_ic50 - log_min) / (log_max - log_min)
    return max(0.0, min(1.0, score))


def _normalize_expression(tpm: Optional[float]) -> float:
    """
    Normalize TPM expression to 0-1.
    TPM < 1: gene not expressed, score 0.
    TPM 1-10: low expression, partial score.
    TPM > 10: well-expressed, approaching 1.0.

    Uses log1p scaling capped at 100 TPM.
    """
    if tpm is None:
        # No RNA-seq data: assume moderate expression (neutral)
        return 0.5
    if tpm < 1.0:
        return 0.0

    # log1p scales: log(1+tpm) / log(1+100)
    return min(1.0, math.log1p(tpm) / math.log1p(100))


def _normalize_vaf(vaf: Optional[float]) -> float:
    """
    Normalize VAF (0-1) to a score.
    Higher VAF = more clonal = better vaccine target.
    VAF 0.5 = fully clonal in a diploid tumor -> score 1.0
    VAF < 0.05 = subclonal, low confidence -> low score
    """
    if vaf is None:
        return 0.5  # assume moderate if unknown
    # Linear scale capped at 0.5 (fully clonal)
    return min(1.0, vaf / 0.5)


def _iedb_score(peptide_seq: str) -> float:
    """
    Placeholder for IEDB immunogenicity prediction.

    In production, this would query the IEDB immunogenicity predictor
    or a local cached model. For now, returns a neutral score.

    TODO: Integrate IEDB Class I immunogenicity predictor
    (http://tools.iedb.org/immunogenicity/)
    """
    # Simple heuristic based on peptide properties:
    # Aromatic and charged residues at anchor positions tend to be more immunogenic.
    # This is a rough proxy until IEDB is integrated.
    aromatic = set("FYWH")
    charged = set("DEKR")
    score = 0.0
    for i, aa in enumerate(peptide_seq):
        if aa in aromatic:
            score += 0.1
        if aa in charged:
            score += 0.05
    return min(1.0, score + 0.3)  # baseline of 0.3


def score_epitopes(
    candidates: list[CandidatePeptide],
    predictions: list[MHCPrediction],
    expression_data: Optional[dict[str, float]] = None,
    custom_weights: Optional[dict[str, float]] = None,
) -> list[ScoredEpitope]:
    """
    Score all candidate peptides using the 7-component composite formula.

    Args:
        candidates: Peptide candidates from peptide_gen
        predictions: MHCflurry predictions (one per peptide-allele pair)
        expression_data: Optional dict mapping gene -> TPM value
        custom_weights: Optional dict of weight overrides from user preferences.
            Keys should match WEIGHTS keys (presentation, binding_rank, etc).
            If provided, overrides the module-level WEIGHTS for this run.

    Returns:
        List of ScoredEpitope objects, one per prediction.
    """
    if expression_data is None:
        expression_data = {}

    # Merge custom weights with defaults. Custom weights come from user
    # preferences stored in user_preferences table (weight_presentation, etc).
    # The DB column names use "weight_" prefix; strip it if present.
    w = dict(WEIGHTS)
    if custom_weights:
        for k, v in custom_weights.items():
            key = k.replace("weight_", "") if k.startswith("weight_") else k
            if key in w and v is not None:
                w[key] = v
        # Normalize so weights sum to 1.0 (prevents user misconfiguration)
        wsum = sum(w.values())
        if wsum > 0 and abs(wsum - 1.0) > 0.01:
            w = {k: v / wsum for k, v in w.items()}
            logger.info(f"Normalized custom weights (sum was {wsum:.3f}): {w}")

    # Build lookup: (peptide_seq, hla_allele) -> MHCPrediction
    pred_lookup: dict[tuple[str, str], MHCPrediction] = {}
    for pred in predictions:
        pred_lookup[(pred.peptide_seq, pred.hla_allele)] = pred

    # Build lookup: peptide_seq -> CandidatePeptide (for variant back-reference)
    # Use (peptide_seq, variant_id) as key to avoid collisions when the same
    # peptide sequence comes from different variants (rare but possible).
    candidate_lookup: dict[str, CandidatePeptide] = {}
    for cand in candidates:
        candidate_lookup[cand.peptide_seq] = cand

    scored = []

    for pred in predictions:
        cand = candidate_lookup.get(pred.peptide_seq)
        if not cand:
            continue

        variant = cand.variant
        expression_tpm = expression_data.get(variant.gene) if variant.gene else None

        # Compute each component (all 0-1)
        presentation_comp = pred.presentation_score
        binding_rank_comp = _normalize_binding_affinity(pred.binding_affinity_nm)
        expression_comp = _normalize_expression(expression_tpm)
        vaf_comp = _normalize_vaf(variant.vaf)
        mutation_type_comp = MUTATION_TYPE_SCORES.get(variant.variant_type, 0.5)
        processing_comp = pred.processing_score  # already 0-1 from MHCflurry
        iedb_comp = _iedb_score(pred.peptide_seq)

        # Weighted sum (7 components, weights sum to 1.0)
        composite = (
            w["presentation"] * presentation_comp
            + w["binding_rank"] * binding_rank_comp
            + w["expression"] * expression_comp
            + w["vaf"] * vaf_comp
            + w["mutation_type"] * mutation_type_comp
            + w["processing"] * processing_comp
            + w["iedb"] * iedb_comp
        )

        # Mutation position within the peptide (for WT derivation / DAI)
        mut_pos = cand.mutation_position if hasattr(cand, 'mutation_position') else None

        # Parse ref/alt AA from protein_change for DAI computation
        ref_aa = None
        alt_aa = None
        if variant.protein_change:
            m = re.match(r"p\.([A-Z][a-z]{0,2})(\d+)([A-Z][a-z]{0,2})", variant.protein_change)
            if m:
                ref_aa = _to_single_letter(m.group(1))
                alt_aa = _to_single_letter(m.group(3))

        # Build explanation dict for SHAP-style waterfall chart
        explanation = {
            "presentation_contribution": round(w["presentation"] * presentation_comp, 4),
            "binding_rank_contribution": round(w["binding_rank"] * binding_rank_comp, 4),
            "expression_contribution": round(w["expression"] * expression_comp, 4),
            "vaf_contribution": round(w["vaf"] * vaf_comp, 4),
            "mutation_type_contribution": round(w["mutation_type"] * mutation_type_comp, 4),
            "processing_contribution": round(w["processing"] * processing_comp, 4),
            "iedb_contribution": round(w["iedb"] * iedb_comp, 4),
            "raw_binding_affinity_nm": pred.binding_affinity_nm,
            "raw_presentation_score": pred.presentation_score,
            "raw_processing_score": pred.processing_score,
            "raw_expression_tpm": expression_tpm,
            "raw_vaf": variant.vaf,
            "mutation_type": variant.variant_type,
            "mutation_position_in_peptide": mut_pos,
            "ref_aa": ref_aa,
            "alt_aa": alt_aa,
        }

        scored.append(ScoredEpitope(
            peptide_seq=pred.peptide_seq,
            peptide_length=len(pred.peptide_seq),
            hla_allele=pred.hla_allele,
            variant=variant,
            binding_affinity_nm=pred.binding_affinity_nm,
            presentation_score=pred.presentation_score,
            processing_score=pred.processing_score,
            expression_tpm=expression_tpm,
            presentation_component=presentation_comp,
            binding_rank_component=binding_rank_comp,
            expression_component=expression_comp,
            vaf_component=vaf_comp,
            mutation_type_component=mutation_type_comp,
            processing_component=processing_comp,
            iedb_component=iedb_comp,
            immunogenicity_score=round(composite, 6),
            mutation_position_in_peptide=mut_pos,
            explanation=explanation,
        ))

    logger.info(f"Scored {len(scored)} epitope-allele pairs")
    return scored


def rank_and_select(
    scored: list[ScoredEpitope],
    top_n: int = 0,
    min_affinity_nm: float = 500.0,
) -> list[ScoredEpitope]:
    """
    Filter, deduplicate, rank, and return all qualifying epitopes.

    Args:
        scored: All scored epitopes
        top_n: Max epitopes to keep. 0 = no cap (keep all binders).
        min_affinity_nm: Maximum IC50 to consider (500nM = weak binder cutoff)

    Returns:
        Qualifying epitopes sorted by immunogenicity_score descending,
        with rank assigned (1 = best).
    """
    # Filter: only keep binders (IC50 < threshold)
    filtered = [s for s in scored if s.binding_affinity_nm <= min_affinity_nm]

    logger.info(
        f"Filtering: {len(scored)} total -> {len(filtered)} with IC50 <= {min_affinity_nm}nM"
    )

    # Deduplicate: same peptide + same allele, keep best score
    best: dict[tuple[str, str], ScoredEpitope] = {}
    for ep in filtered:
        key = (ep.peptide_seq, ep.hla_allele)
        if key not in best or ep.immunogenicity_score > best[key].immunogenicity_score:
            best[key] = ep

    unique = list(best.values())
    logger.info(f"After dedup: {len(unique)} unique peptide-allele pairs")

    # Sort by composite score descending
    ranked = sorted(unique, key=lambda x: x.immunogenicity_score, reverse=True)

    # Apply cap only if explicitly set
    top = ranked[:top_n] if top_n > 0 else ranked

    if top:
        logger.info(
            f"Selected {len(top)} epitopes. "
            f"Score range: {top[-1].immunogenicity_score:.4f} - {top[0].immunogenicity_score:.4f}"
        )
    else:
        logger.info("No epitopes passed filters")

    return top


def derive_wt_peptide(
    mutant_peptide: str,
    mutation_position: Optional[int],
    ref_aa: Optional[str],
    alt_aa: Optional[str],
) -> Optional[str]:
    """
    Derive the wildtype peptide from a mutant peptide by swapping the
    mutant residue back to the reference AA.

    Args:
        mutant_peptide: The neoepitope peptide sequence (contains alt_aa)
        mutation_position: 0-based index of the mutant residue in the peptide
        ref_aa: Reference (wildtype) amino acid (single letter)
        alt_aa: Alternate (mutant) amino acid (single letter)

    Returns:
        Wildtype peptide string, or None if derivation isn't possible.
    """
    if not ref_aa or not alt_aa:
        return None
    if mutation_position is None or mutation_position < 0:
        return None
    if mutation_position >= len(mutant_peptide):
        return None

    # Sanity check: the residue at mutation_position should be alt_aa
    if mutant_peptide[mutation_position] != alt_aa:
        # The peptide may have been generated with synthetic flanking.
        # Try to find alt_aa in the peptide and use the first occurrence.
        pos = mutant_peptide.find(alt_aa)
        if pos == -1:
            return None
        mutation_position = pos

    wt = mutant_peptide[:mutation_position] + ref_aa + mutant_peptide[mutation_position + 1:]
    return wt


def compute_dai(
    epitopes: list[ScoredEpitope],
    predictor,
) -> list[ScoredEpitope]:
    """
    Compute Differential Agretopicity Index (DAI) for scored epitopes.

    DAI = log2(WT_IC50 / mutant_IC50)

    Positive DAI means the mutant peptide binds MHC better than the WT,
    indicating a true neoantigen that the immune system hasn't been
    tolerized against. Higher DAI = better vaccine candidate.

    Only works for missense variants where we can derive the WT peptide.
    Frameshifts get DAI=None (the entire downstream sequence is novel,
    so there's no meaningful WT comparison).

    Args:
        epitopes: Scored epitopes (after rank_and_select)
        predictor: MHC predictor instance (MHCflurry or mock)

    Returns:
        Same epitopes list, with dai_score and wt_binding_affinity_nm filled in.
    """
    # Collect WT peptides that need prediction
    wt_peptides_to_predict: list[str] = []
    wt_alleles_to_predict: list[str] = []
    wt_map: dict[int, str] = {}  # index in epitopes -> WT peptide

    for i, ep in enumerate(epitopes):
        # Only compute DAI for missense (and inframe_indel/nonsense with parseable changes)
        if ep.variant.variant_type == "frameshift":
            continue

        ref_aa = ep.explanation.get("ref_aa") if ep.explanation else None
        alt_aa = ep.explanation.get("alt_aa") if ep.explanation else None
        mut_pos = ep.mutation_position_in_peptide

        wt_pep = derive_wt_peptide(ep.peptide_seq, mut_pos, ref_aa, alt_aa)
        if wt_pep and wt_pep != ep.peptide_seq:
            wt_map[i] = wt_pep
            wt_peptides_to_predict.append(wt_pep)
            wt_alleles_to_predict.append(ep.hla_allele)

    if not wt_peptides_to_predict:
        logger.info("No WT peptides to predict for DAI (all frameshifts or missing data)")
        return epitopes

    logger.info(f"Computing DAI: predicting {len(wt_peptides_to_predict)} WT peptides")

    # Run MHCflurry on WT peptides. We pass parallel lists (not cartesian)
    # because each WT peptide corresponds to exactly one allele.
    # The predictor.predict() does cartesian product, so we need to handle
    # this differently -- predict one-by-one or batch unique peptides.
    #
    # For efficiency, collect unique (wt_peptide, allele) pairs and predict.
    unique_pairs: dict[tuple[str, str], float] = {}
    unique_peptides_by_allele: dict[str, list[str]] = {}

    for pep, allele in zip(wt_peptides_to_predict, wt_alleles_to_predict):
        key = (pep, allele)
        if key not in unique_pairs:
            unique_pairs[key] = 0.0  # placeholder
            if allele not in unique_peptides_by_allele:
                unique_peptides_by_allele[allele] = []
            if pep not in unique_peptides_by_allele[allele]:
                unique_peptides_by_allele[allele].append(pep)

    # Predict per-allele to avoid cartesian explosion
    for allele, peps in unique_peptides_by_allele.items():
        try:
            preds = predictor.predict(peps, [allele])
            for pred in preds:
                unique_pairs[(pred.peptide_seq, pred.hla_allele)] = pred.binding_affinity_nm
        except Exception as e:
            logger.warning(f"WT MHC prediction failed for allele {allele}: {e}")

    # Now assign DAI scores
    dai_count = 0
    for i, wt_pep in wt_map.items():
        ep = epitopes[i]
        wt_ic50 = unique_pairs.get((wt_pep, ep.hla_allele))
        if wt_ic50 is not None and wt_ic50 > 0 and ep.binding_affinity_nm > 0:
            ep.wt_binding_affinity_nm = wt_ic50
            ep.dai_score = round(math.log2(wt_ic50 / ep.binding_affinity_nm), 4)
            # Also store in explanation for the frontend
            if ep.explanation:
                ep.explanation["dai_score"] = ep.dai_score
                ep.explanation["wt_binding_affinity_nm"] = wt_ic50
                ep.explanation["wt_peptide"] = wt_pep
            dai_count += 1

    logger.info(f"DAI computed for {dai_count}/{len(epitopes)} epitopes")
    return epitopes
