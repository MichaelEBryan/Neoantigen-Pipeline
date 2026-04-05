import asyncio
import logging
from celery import Celery
from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery("cvdash")

celery_app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


def _run_async(coro):
    """Run an async coroutine from sync Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def process_vcf_analysis(
    self,
    analysis_id: int,
    vcf_file_path: str | None = None,
    hla_alleles: list[str] | None = None,
    expression_data: dict[str, float] | None = None,
    use_mock_predictor: bool = False,
) -> dict:
    """
    Celery task: run the full VCF-to-epitope pipeline.

    Called when a new VCF analysis is submitted. Runs asynchronously
    in a Celery worker process.

    Args:
        analysis_id: DB ID of the Analysis record
        vcf_file_path: Path to the annotated VCF file. If None, resolved from
                       analysis_inputs table (file_type='vcf').
        hla_alleles: Patient HLA alleles. If None, loaded from hla_types table.
        expression_data: Optional gene -> TPM map. If None but an expression_matrix
                         input exists in DB, it gets parsed at runtime.
        use_mock_predictor: Use mock MHCflurry (for testing)
    """
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlalchemy import select
    from app.models import Analysis, AnalysisInput, HLAType, UserPreferences
    from app.pipeline.orchestrator import run_pipeline

    async def _run():
        engine = create_async_engine(settings.database_url, echo=False)
        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)

            async with session_factory() as db:
                # Load analysis
                stmt = select(Analysis).where(Analysis.id == analysis_id)
                result = await db.execute(stmt)
                analysis = result.scalar_one_or_none()

                if not analysis:
                    raise ValueError(f"Analysis {analysis_id} not found")

                # Resolve input file path from DB if not explicitly passed.
                # Supports both VCF and MAF file types.
                vcf_path = vcf_file_path
                if not vcf_path:
                    # Try VCF first, then MAF
                    for ftype in ("vcf", "maf"):
                        input_stmt = select(AnalysisInput).where(
                            AnalysisInput.analysis_id == analysis_id,
                            AnalysisInput.file_type == ftype,
                        )
                        input_result = await db.execute(input_stmt)
                        found_input = input_result.scalar_one_or_none()
                        if found_input:
                            vcf_path = found_input.file_path
                            logger.info(
                                f"Resolved {ftype} input for analysis {analysis_id}: "
                                f"{vcf_path}"
                            )
                            break

                    if not vcf_path:
                        raise ValueError(
                            f"No VCF or MAF file found for analysis {analysis_id}. "
                            "Upload a mutation file before submitting."
                        )

                # If HLA alleles not passed, load from DB
                alleles = hla_alleles
                if not alleles:
                    hla_stmt = select(HLAType).where(HLAType.analysis_id == analysis_id)
                    hla_result = await db.execute(hla_stmt)
                    alleles = [h.allele for h in hla_result.scalars().all()]

                if not alleles:
                    raise ValueError(f"No HLA alleles for analysis {analysis_id}")

                # Load expression data from DB if not passed but a matrix was uploaded
                expr_data = expression_data
                if expr_data is None:
                    expr_stmt = select(AnalysisInput).where(
                        AnalysisInput.analysis_id == analysis_id,
                        AnalysisInput.file_type == "expression_matrix",
                    )
                    expr_result = await db.execute(expr_stmt)
                    expr_input = expr_result.scalar_one_or_none()
                    if expr_input:
                        try:
                            from app.pipeline.expression_parser import parse_expression_matrix
                            expr_data = parse_expression_matrix(expr_input.file_path)
                            logger.info(
                                f"Loaded expression matrix from DB: "
                                f"{len(expr_data)} genes"
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to parse expression matrix: {e}. "
                                "Proceeding without expression data."
                            )

                # Load user's custom scoring weights if they've set any
                custom_weights = None
                if analysis.user_id:
                    prefs_stmt = select(UserPreferences).where(
                        UserPreferences.user_id == analysis.user_id
                    )
                    prefs_result = await db.execute(prefs_stmt)
                    prefs = prefs_result.scalar_one_or_none()
                    if prefs:
                        w = {}
                        for key in ("presentation", "binding_rank", "expression",
                                    "vaf", "mutation_type", "processing", "iedb"):
                            val = getattr(prefs, f"weight_{key}", None)
                            if val is not None:
                                w[key] = val
                        if w:
                            custom_weights = w

                epitope_count = await run_pipeline(
                    db=db,
                    analysis=analysis,
                    vcf_path=vcf_path,
                    hla_alleles=alleles,
                    expression_data=expr_data,
                    use_mock_predictor=use_mock_predictor,
                    custom_weights=custom_weights,
                )

                return epitope_count
        finally:
            await engine.dispose()

    try:
        count = _run_async(_run())
        return {
            "status": "complete",
            "analysis_id": analysis_id,
            "epitope_count": count,
        }
    except Exception as e:
        logger.error(f"Celery task failed for analysis {analysis_id}: {e}")
        return {
            "status": "failed",
            "analysis_id": analysis_id,
            "error": str(e),
        }


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def process_remote_analysis(
    self,
    analysis_id: int,
    backend_name: str | None = None,
    hla_alleles: list[str] | None = None,
    use_mock_predictor: bool = False,
) -> dict:
    """
    Celery task: full FASTQ/BAM-to-epitope pipeline via remote compute.

    This is the end-to-end task for analyses that need alignment and
    variant calling (FASTQ or BAM input). It:

      1. Dispatches the variant-calling job to the configured backend
         (GCP Batch or Isambard). This blocks (with polling) until the
         remote job finishes and the VCF is downloaded.
      2. Runs the local scoring pipeline (same as process_vcf_analysis)
         on the resulting VCF.

    Args:
        analysis_id: DB ID of the Analysis record
        backend_name: "gcp-batch" or "isambard" (None = read from config)
        hla_alleles: Override HLA alleles (None = read from DB)
        use_mock_predictor: Use mock MHCflurry (for testing)
    """
    from app.compute.dispatch import dispatch_and_wait
    from app.compute.backend import get_compute_backend

    async def _run():
        # Phase 1: Remote variant calling (upload, run, download VCF)
        backend = get_compute_backend(backend_name)
        vcf_path = await dispatch_and_wait(analysis_id, backend=backend)

        # Phase 2: Local scoring pipeline (same as process_vcf_analysis)
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy import select
        from app.models import Analysis, HLAType, UserPreferences
        from app.pipeline.orchestrator import run_pipeline

        engine = create_async_engine(settings.database_url, echo=False)
        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                stmt = select(Analysis).where(Analysis.id == analysis_id)
                result = await db.execute(stmt)
                analysis = result.scalar_one_or_none()

                if not analysis:
                    raise ValueError(f"Analysis {analysis_id} not found")

                # Load HLA alleles from DB if not passed
                alleles = hla_alleles
                if not alleles:
                    hla_stmt = select(HLAType).where(
                        HLAType.analysis_id == analysis_id
                    )
                    hla_result = await db.execute(hla_stmt)
                    alleles = [h.allele for h in hla_result.scalars().all()]

                if not alleles:
                    raise ValueError(f"No HLA alleles for analysis {analysis_id}")

                # Load user's custom scoring weights
                custom_weights = None
                if analysis.user_id:
                    prefs_stmt = select(UserPreferences).where(
                        UserPreferences.user_id == analysis.user_id
                    )
                    prefs_result = await db.execute(prefs_stmt)
                    prefs = prefs_result.scalar_one_or_none()
                    if prefs:
                        w = {}
                        for key in ("presentation", "binding_rank", "expression",
                                    "vaf", "mutation_type", "processing", "iedb"):
                            val = getattr(prefs, f"weight_{key}", None)
                            if val is not None:
                                w[key] = val
                        if w:
                            custom_weights = w

                epitope_count = await run_pipeline(
                    db=db,
                    analysis=analysis,
                    vcf_path=vcf_path,
                    hla_alleles=alleles,
                    use_mock_predictor=use_mock_predictor,
                    custom_weights=custom_weights,
                )
                return epitope_count
        finally:
            await engine.dispose()

    try:
        count = _run_async(_run())
        return {
            "status": "complete",
            "analysis_id": analysis_id,
            "epitope_count": count,
        }
    except Exception as e:
        logger.error(f"Remote analysis task failed for {analysis_id}: {e}")
        return {
            "status": "failed",
            "analysis_id": analysis_id,
            "error": str(e),
        }
