"""
Pipeline orchestrator.

Runs the full VCF-to-ranked-epitopes pipeline:
  1. Parse VCF -> coding variants
  2. Generate candidate peptides (8-11mers)
  3. Run MHCflurry predictions
  4. Score with composite formula
  5. Rank and select top binders (up to top_n, default 500)
  6. Write results to database

Each step logs progress to the JobLog table so the frontend
can show real-time pipeline status.

Can be called directly or via Celery task.
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Analysis, Variant, Epitope, HLAType, JobLog,
)

from .vcf_parser import parse_vcf
from .maf_parser import parse_maf
from .peptide_gen import generate_peptides
from .mhc_predict import get_predictor, BaseMHCPredictor, MHCflurryPredictor
from .scorer import score_epitopes, rank_and_select, compute_dai
from .progress import publish_progress, publish_terminal

logger = logging.getLogger(__name__)


def _validate_hla_alleles(hla_alleles: list[str], predictor: BaseMHCPredictor) -> tuple[bool, Optional[str]]:
    """
    Validate HLA alleles before prediction.

    Args:
        hla_alleles: List of HLA allele strings
        predictor: MHC predictor instance

    Returns:
        (is_valid, error_message) - is_valid True if all checks pass, error_message set if validation fails
    """
    if not hla_alleles:
        return False, "HLA alleles list is empty"

    # Validate format: should be HLA-X*NN:NN or similar
    hla_format_re = re.compile(r"^HLA-[A-Z]\*\d{2}:\d{2}(:\d{2})?$", re.IGNORECASE)

    invalid_alleles = []
    for allele in hla_alleles:
        if not hla_format_re.match(allele.strip()):
            invalid_alleles.append(allele)

    if invalid_alleles:
        return False, (
            f"Invalid HLA allele format(s): {', '.join(invalid_alleles[:3])}"
            f"{'...' if len(invalid_alleles) > 3 else ''}. "
            f"Expected format: HLA-X*NN:NN (e.g., HLA-A*02:01)"
        )

    # If using real MHCflurry, check if alleles are supported
    if isinstance(predictor, MHCflurryPredictor):
        try:
            from mhcflurry import Class1PresentationPredictor
            # Load briefly to get supported alleles
            real_predictor = Class1PresentationPredictor.load()
            supported = set(real_predictor.alleles)

            unsupported = []
            for allele in hla_alleles:
                allele_upper = allele.upper()
                # MHCflurry uses uppercase names like "HLA-A*02:01"
                if allele_upper not in supported:
                    unsupported.append(allele)

            if unsupported:
                return False, (
                    f"HLA allele(s) not supported by MHCflurry: {', '.join(unsupported[:3])}. "
                    f"Check that allele names match the MHCflurry reference (e.g., HLA-A*02:01, HLA-B*44:02). "
                    f"Run `mhcflurry-downloads fetch models_class1_presentation` to ensure models are installed."
                )
        except Exception as e:
            # If we can't validate with real predictor, just warn
            logger.warning(f"Could not validate HLA alleles against MHCflurry: {e}")

    return True, None

# Import the canonical step definitions from ws.py to avoid weight drift.
# Lazy import to avoid circular dependency (ws imports progress, orchestrator imports progress).
_STEP_PROGRESS: dict[str, float] | None = None


def _get_step_progress() -> dict[str, float]:
    """Lazy-load cumulative progress map from ws.py PIPELINE_STEPS."""
    global _STEP_PROGRESS
    if _STEP_PROGRESS is None:
        # Build cumulative map from the canonical PIPELINE_STEPS
        # These are defined in ws.py and analyses.py (identical).
        # We replicate the cumulative calculation here.
        steps = [
            ("upload_received", 0.02),
            ("vcf_parsing", 0.08),
            ("variant_storage", 0.05),
            ("peptide_generation", 0.10),
            ("mhc_prediction", 0.40),
            ("scoring", 0.15),
            ("ranking", 0.05),
            ("results_storage", 0.10),
            ("done", 0.05),
        ]
        cumulative = {}
        total = 0.0
        for key, weight in steps:
            total += weight
            cumulative[key] = round(total, 3)
        _STEP_PROGRESS = cumulative
    return _STEP_PROGRESS


async def _log_step(
    db: AsyncSession,
    analysis_id: int,
    step: str,
    status: str,
    message: str,
) -> None:
    """Write a job log entry and flush immediately for real-time visibility.
    Also publishes to Redis pub/sub so WebSocket clients get live updates."""
    log = JobLog(
        analysis_id=analysis_id,
        step=step,
        status=status,
        message=message,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(log)
    await db.flush()

    # Calculate progress based on step completion
    sp = _get_step_progress()
    if status == "complete" and step in sp:
        pct = sp[step]
    elif status == "running" and step in sp:
        # Halfway into this step
        keys = list(sp.keys())
        idx = keys.index(step)
        prev_pct = sp[keys[idx - 1]] if idx > 0 else 0.0
        pct = (prev_pct + sp[step]) / 2
    else:
        pct = 0.0

    await publish_progress(analysis_id, step, status, message, pct)


async def run_pipeline(
    db: AsyncSession,
    analysis: Analysis,
    vcf_path: str | Path,
    hla_alleles: list[str],
    expression_data: Optional[dict[str, float]] = None,
    use_mock_predictor: bool = False,
    top_n: int = 0,  # 0 = no cap, keep all binders
    min_affinity_nm: float = 500.0,
    min_vaf: float = 0.0,
    custom_weights: Optional[dict[str, float]] = None,
) -> int:
    """
    Run the full VCF/MAF-to-epitope pipeline for an analysis.

    Args:
        db: Async database session
        analysis: Analysis ORM object (must already exist in DB)
        vcf_path: Path to annotated VCF or MAF file
        hla_alleles: Patient HLA alleles (e.g. ["HLA-A*02:01", "HLA-B*44:02"])
        expression_data: Optional gene -> TPM mapping from RNA-seq
        use_mock_predictor: Use mock MHCflurry for testing
        top_n: Number of top epitopes to keep
        min_affinity_nm: IC50 cutoff for weak binders (default 500nM)
        min_vaf: Minimum VAF to include variants

    Returns:
        Number of epitopes stored in database.

    Raises:
        Exception: Pipeline failures are caught, logged, and re-raised.
                   Analysis status is set to 'failed' on error.
    """
    try:
        # Mark analysis as running
        analysis.status = "running"
        await db.flush()

        # -- Step 1: Parse input file (VCF or MAF) --
        # Detect format by file extension
        input_path = Path(vcf_path)
        is_maf = input_path.suffix.lower() in (".maf", ".txt")

        try:
            if is_maf:
                await _log_step(db, analysis.id, "vcf_parsing", "running",
                                "Parsing MAF mutation file...")
                parse_result = parse_maf(vcf_path, min_vaf=min_vaf)
            else:
                await _log_step(db, analysis.id, "vcf_parsing", "running",
                                "Parsing VCF file...")
                parse_result = parse_vcf(vcf_path, min_vaf=min_vaf)
        except FileNotFoundError:
            await _log_step(db, analysis.id, "vcf_parsing", "complete",
                            "File appears empty or could not be read. Please check the file path and try again.")
            analysis.status = "complete"
            analysis.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return 0
        except Exception as e:
            error_msg = str(e)
            if "no data records" in error_msg.lower() or "empty" in error_msg.lower():
                detail = "VCF file has no data records. Check that the file is properly formatted."
            elif "format" in error_msg.lower():
                detail = "File format not recognized. Ensure the file is a valid VCF or MAF format."
            else:
                detail = f"Error parsing file: {error_msg}"
            await _log_step(db, analysis.id, "vcf_parsing", "complete", detail)
            analysis.status = "complete"
            analysis.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return 0

        variants = parse_result.variants

        if not variants:
            # Build informative message about what was found
            consequence_summary = ""
            if parse_result.consequence_counts:
                found_terms = sorted(parse_result.consequence_counts.items(), key=lambda x: -x[1])
                consequence_summary = (
                    f"\n\nConsequence types found in file: "
                    f"{', '.join(f'{term} ({count})' for term, count in found_terms[:5])}"
                )
                if len(found_terms) > 5:
                    consequence_summary += f" (and {len(found_terms) - 5} more)"

            message = (
                f"No coding variants found in file. Total records processed: {parse_result.total_records}. "
                f"Skipped: {parse_result.skipped_noncoding} non-coding, "
                f"{parse_result.skipped_vaf} low-VAF, {parse_result.skipped_filter} filtered."
                f"{consequence_summary}\n\n"
                f"All variants in this file are non-coding (intergenic, intronic, silent, etc.). "
                f"This may indicate:\n"
                f"  - Low tumor mutational burden sample\n"
                f"  - VCF needs annotation with consequence predictions (VEP, SnpEff, Funcotator)\n"
                f"  - VCF is filtered to exclude non-coding variants\n\n"
                f"To proceed, ensure your VCF is annotated with a tool like VEP or SnpEff and contains coding variant predictions."
            )

            await _log_step(db, analysis.id, "vcf_parsing", "complete", message)
            analysis.status = "complete"
            analysis.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return 0

        await _log_step(
            db, analysis.id, "vcf_parsing", "complete",
            f"Found {len(variants)} coding variants "
            f"({sum(1 for v in variants if v.variant_type == 'missense')} missense, "
            f"{sum(1 for v in variants if v.variant_type == 'frameshift')} frameshift, "
            f"{sum(1 for v in variants if v.variant_type == 'inframe_indel')} inframe indel)"
        )

        # -- Step 2: Store variants in DB --
        await _log_step(db, analysis.id, "variant_storage", "running", "Storing variants...")

        variant_map = {}  # ParsedVariant -> Variant ORM object
        for pv in variants:
            db_variant = Variant(
                analysis_id=analysis.id,
                chrom=pv.chrom,
                pos=pv.pos,
                ref=pv.ref,
                alt=pv.alt,
                gene=pv.gene,
                protein_change=pv.protein_change,
                variant_type=pv.variant_type,
                vaf=pv.vaf,
                annotation_json=pv.annotation,
            )
            db.add(db_variant)
            variant_map[id(pv)] = db_variant

        await db.flush()  # get variant IDs
        await _log_step(db, analysis.id, "variant_storage", "complete",
                        f"Stored {len(variants)} variants")

        # -- Step 3: Generate peptides --
        await _log_step(db, analysis.id, "peptide_generation", "running",
                        "Generating candidate peptides (8-11mers)...")

        # In this sandbox, pyensembl data likely isn't installed.
        # The pipeline works with or without it (falls back to HGVS-only approach).
        candidates = generate_peptides(variants, use_pyensembl=False)

        if not candidates:
            message = (
                f"No candidate peptides could be generated from {len(variants)} variants. "
                f"This typically means the protein change annotations (HGVSp) are missing or could not be parsed.\n\n"
                f"To fix this:\n"
                f"  1. Ensure the VCF was annotated with VEP, SnpEff, Funcotator, or similar tool\n"
                f"  2. Verify the HGVSp field contains protein change predictions (e.g., p.V600E)\n"
                f"  3. Check that variant annotations use standard consequence terms (missense_variant, frameshift_variant, etc.)\n"
                f"  4. Re-annotate your VCF if necessary: maf2maf --maf yourfile.maf --vep"
            )
            await _log_step(db, analysis.id, "peptide_generation", "complete", message)
            analysis.status = "complete"
            analysis.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return 0

        await _log_step(
            db, analysis.id, "peptide_generation", "complete",
            f"Generated {len(candidates)} candidate peptides from {len(variants)} variants"
        )

        # -- Step 4: MHCflurry prediction --
        await _log_step(db, analysis.id, "mhc_prediction", "running",
                        f"Running MHC binding predictions ({len(candidates)} peptides x {len(hla_alleles)} alleles)...")

        predictor = get_predictor(use_mock=use_mock_predictor)

        # Validate HLA alleles before prediction
        is_valid, error_msg = _validate_hla_alleles(hla_alleles, predictor)
        if not is_valid:
            await _log_step(db, analysis.id, "mhc_prediction", "complete", error_msg)
            analysis.status = "complete"
            analysis.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return 0

        peptide_seqs = list({c.peptide_seq for c in candidates})  # unique peptides
        predictions = predictor.predict(peptide_seqs, hla_alleles)

        await _log_step(
            db, analysis.id, "mhc_prediction", "complete",
            f"Completed {len(predictions)} binding predictions"
        )

        # -- Step 5: Score --
        await _log_step(db, analysis.id, "scoring", "running", "Computing composite immunogenicity scores...")

        scored = score_epitopes(candidates, predictions, expression_data, custom_weights=custom_weights)

        await _log_step(
            db, analysis.id, "scoring", "complete",
            f"Scored {len(scored)} peptide-allele pairs"
        )

        # -- Step 6: Rank and filter --
        await _log_step(db, analysis.id, "ranking", "running",
                        f"Ranking and selecting top {top_n} epitopes...")

        top_epitopes = rank_and_select(scored, top_n=top_n, min_affinity_nm=min_affinity_nm)

        await _log_step(
            db, analysis.id, "ranking", "complete",
            f"Selected {len(top_epitopes)} epitopes from {len(scored)} scored pairs (IC50 <= {min_affinity_nm}nM)"
        )

        # -- Step 6b: Compute DAI (Differential Agretopicity Index) --
        # Compare mutant vs wildtype MHC binding for each epitope.
        # This runs a second pass of MHCflurry on the derived WT peptides.
        try:
            top_epitopes = compute_dai(top_epitopes, predictor)
            dai_count = sum(1 for e in top_epitopes if e.dai_score is not None)
            logger.info(f"DAI computed for {dai_count}/{len(top_epitopes)} epitopes")
        except Exception as e:
            logger.warning(f"DAI computation failed (non-fatal): {e}")
            # DAI failure is non-fatal -- epitopes still valid without it

        # -- Step 7: Write epitopes to DB --
        await _log_step(db, analysis.id, "results_storage", "running", "Storing results...")

        for rank, ep in enumerate(top_epitopes, start=1):
            # Find the DB variant object for this epitope's source variant
            db_variant = variant_map.get(id(ep.variant))
            if not db_variant:
                logger.warning(f"No DB variant found for epitope {ep.peptide_seq}")
                continue

            db_epitope = Epitope(
                analysis_id=analysis.id,
                variant_id=db_variant.id,
                peptide_seq=ep.peptide_seq,
                peptide_length=ep.peptide_length,
                hla_allele=ep.hla_allele,
                binding_affinity_nm=ep.binding_affinity_nm,
                presentation_score=ep.presentation_score,
                processing_score=ep.processing_score,
                expression_tpm=ep.expression_tpm,
                immunogenicity_score=ep.immunogenicity_score,
                dai_score=ep.dai_score,
                wt_binding_affinity_nm=ep.wt_binding_affinity_nm,
                rank=rank,
                explanation_json=ep.explanation,
            )
            db.add(db_epitope)

        # Mark complete
        analysis.status = "complete"
        analysis.completed_at = datetime.now(timezone.utc)

        await _log_step(
            db, analysis.id, "results_storage", "complete",
            f"Pipeline complete. {len(top_epitopes)} epitopes stored (rank 1-{len(top_epitopes)})."
        )

        await db.commit()

        logger.info(
            f"Pipeline complete for analysis {analysis.id}: "
            f"{len(variants)} variants -> {len(candidates)} peptides -> "
            f"{len(predictions)} predictions -> {len(top_epitopes)} top epitopes"
        )

        await publish_terminal(
            analysis.id, "complete",
            f"Pipeline complete. {len(top_epitopes)} epitopes ranked.",
        )

        return len(top_epitopes)

    except Exception as e:
        logger.error(f"Pipeline failed for analysis {analysis.id}: {e}", exc_info=True)

        try:
            analysis.status = "failed"
            await _log_step(db, analysis.id, "error", "failed", str(e))
            await db.commit()
        except Exception:
            # If we can't even log the failure, just rollback
            await db.rollback()

        await publish_terminal(analysis.id, "failed", str(e))

        raise
