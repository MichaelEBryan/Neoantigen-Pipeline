"""
Seed script for CVDash development database.

Creates:
- 1 test user (demo@cvdash.dev / Password1)
- 1 project (melanoma, GRCh38)
- 1 completed analysis (VCF input)
- 6 HLA alleles (3 Class I loci, 2 alleles each)
- 5 somatic variants (realistic melanoma mutations)
- 20 epitopes ranked 1-20 with realistic MHCflurry-like scores
- Job logs showing completed pipeline steps

Run: python scripts/seed.py
Requires: DATABASE_URL env var pointing to a running Postgres instance.
"""
import asyncio
import sys
import os

# Add backend root to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.config import settings
from app.auth import hash_password
from app.models import (
    Base, User, Project, Analysis, AnalysisInput, HLAType,
    Variant, Epitope, JobLog,
)


# -- Realistic test data for a melanoma case --

HLA_ALLELES = [
    # Typical Caucasian HLA-A, B, C genotype
    ("HLA-A*02:01", "provided"),
    ("HLA-A*01:01", "provided"),
    ("HLA-B*44:02", "provided"),
    ("HLA-B*08:01", "provided"),
    ("HLA-C*05:01", "provided"),
    ("HLA-C*07:01", "provided"),
]

# Real melanoma driver genes with plausible mutations
VARIANTS = [
    {
        "chrom": "chr7", "pos": 140753336, "ref": "A", "alt": "T",
        "gene": "BRAF", "protein_change": "p.V600E", "variant_type": "missense",
        "vaf": 0.42, "annotation_json": {"consequence": "missense_variant", "impact": "HIGH"},
    },
    {
        "chrom": "chr1", "pos": 115256529, "ref": "G", "alt": "A",
        "gene": "NRAS", "protein_change": "p.Q61K", "variant_type": "missense",
        "vaf": 0.31, "annotation_json": {"consequence": "missense_variant", "impact": "HIGH"},
    },
    {
        "chrom": "chr17", "pos": 7577120, "ref": "C", "alt": "T",
        "gene": "TP53", "protein_change": "p.R248W", "variant_type": "missense",
        "vaf": 0.55, "annotation_json": {"consequence": "missense_variant", "impact": "HIGH"},
    },
    {
        "chrom": "chr10", "pos": 89692905, "ref": "C", "alt": "T",
        "gene": "PTEN", "protein_change": "p.R130Q", "variant_type": "missense",
        "vaf": 0.28, "annotation_json": {"consequence": "missense_variant", "impact": "HIGH"},
    },
    {
        "chrom": "chr12", "pos": 25398284, "ref": "C", "alt": "A",
        "gene": "KRAS", "protein_change": "p.G12V", "variant_type": "missense",
        "vaf": 0.19, "annotation_json": {"consequence": "missense_variant", "impact": "HIGH"},
    },
]

# 20 epitopes across the 5 variants, scored to look like real MHCflurry output.
# Lower binding_affinity_nm = stronger binder. presentation_score 0-1.
# immunogenicity_score is the composite rank metric.
EPITOPES = [
    # BRAF V600E -- strong binder to HLA-A*02:01, well-known neoepitope
    {"variant_idx": 0, "peptide_seq": "LATEKSRWSG", "length": 10, "hla": "HLA-A*02:01",
     "affinity": 12.3, "presentation": 0.95, "processing": 0.88, "expression": 48.2, "score": 0.97},
    {"variant_idx": 0, "peptide_seq": "EKSRWSGSH", "length": 9, "hla": "HLA-A*02:01",
     "affinity": 28.5, "presentation": 0.91, "processing": 0.82, "expression": 48.2, "score": 0.94},
    {"variant_idx": 0, "peptide_seq": "LATEKSRWS", "length": 9, "hla": "HLA-B*44:02",
     "affinity": 45.1, "presentation": 0.87, "processing": 0.79, "expression": 48.2, "score": 0.91},
    {"variant_idx": 0, "peptide_seq": "ATEKSRWSGSH", "length": 11, "hla": "HLA-A*01:01",
     "affinity": 89.4, "presentation": 0.78, "processing": 0.71, "expression": 48.2, "score": 0.85},
    # NRAS Q61K
    {"variant_idx": 1, "peptide_seq": "ILDTAGKEEY", "length": 10, "hla": "HLA-A*02:01",
     "affinity": 18.7, "presentation": 0.93, "processing": 0.85, "expression": 32.1, "score": 0.95},
    {"variant_idx": 1, "peptide_seq": "DTAGKEEYSAM", "length": 11, "hla": "HLA-B*08:01",
     "affinity": 55.2, "presentation": 0.84, "processing": 0.76, "expression": 32.1, "score": 0.88},
    {"variant_idx": 1, "peptide_seq": "TAGKEEYA", "length": 8, "hla": "HLA-C*07:01",
     "affinity": 120.5, "presentation": 0.72, "processing": 0.68, "expression": 32.1, "score": 0.79},
    {"variant_idx": 1, "peptide_seq": "LDTAGKEEY", "length": 9, "hla": "HLA-A*02:01",
     "affinity": 34.8, "presentation": 0.89, "processing": 0.81, "expression": 32.1, "score": 0.92},
    # TP53 R248W
    {"variant_idx": 2, "peptide_seq": "VVWCPHHQG", "length": 9, "hla": "HLA-A*02:01",
     "affinity": 22.1, "presentation": 0.92, "processing": 0.84, "expression": 61.5, "score": 0.93},
    {"variant_idx": 2, "peptide_seq": "GVVWCPHHQG", "length": 10, "hla": "HLA-B*44:02",
     "affinity": 67.3, "presentation": 0.81, "processing": 0.73, "expression": 61.5, "score": 0.86},
    {"variant_idx": 2, "peptide_seq": "VWCPHHQGV", "length": 9, "hla": "HLA-C*05:01",
     "affinity": 145.0, "presentation": 0.69, "processing": 0.65, "expression": 61.5, "score": 0.76},
    {"variant_idx": 2, "peptide_seq": "VVWCPHHQ", "length": 8, "hla": "HLA-A*01:01",
     "affinity": 198.4, "presentation": 0.61, "processing": 0.58, "expression": 61.5, "score": 0.70},
    # PTEN R130Q
    {"variant_idx": 3, "peptide_seq": "YQHTVRGL", "length": 8, "hla": "HLA-A*02:01",
     "affinity": 38.9, "presentation": 0.88, "processing": 0.80, "expression": 25.8, "score": 0.90},
    {"variant_idx": 3, "peptide_seq": "QYQHTVRGLK", "length": 10, "hla": "HLA-B*08:01",
     "affinity": 76.2, "presentation": 0.79, "processing": 0.72, "expression": 25.8, "score": 0.83},
    {"variant_idx": 3, "peptide_seq": "YQHTVRGLL", "length": 9, "hla": "HLA-A*02:01",
     "affinity": 102.3, "presentation": 0.74, "processing": 0.69, "expression": 25.8, "score": 0.80},
    {"variant_idx": 3, "peptide_seq": "YQHTVRGLKV", "length": 10, "hla": "HLA-C*05:01",
     "affinity": 210.5, "presentation": 0.58, "processing": 0.55, "expression": 25.8, "score": 0.67},
    # KRAS G12V
    {"variant_idx": 4, "peptide_seq": "VVGAVGVGK", "length": 9, "hla": "HLA-A*02:01",
     "affinity": 15.6, "presentation": 0.94, "processing": 0.87, "expression": 18.4, "score": 0.96},
    {"variant_idx": 4, "peptide_seq": "VVGAVGVGKS", "length": 10, "hla": "HLA-A*01:01",
     "affinity": 42.7, "presentation": 0.86, "processing": 0.78, "expression": 18.4, "score": 0.89},
    {"variant_idx": 4, "peptide_seq": "VGAVGVGK", "length": 8, "hla": "HLA-B*44:02",
     "affinity": 155.8, "presentation": 0.67, "processing": 0.63, "expression": 18.4, "score": 0.74},
    {"variant_idx": 4, "peptide_seq": "VVGAVGVGKSA", "length": 11, "hla": "HLA-C*07:01",
     "affinity": 230.1, "presentation": 0.55, "processing": 0.52, "expression": 18.4, "score": 0.63},
]

# Pipeline steps that would have been logged during a completed analysis
PIPELINE_STEPS = [
    ("upload_validation", "complete", "VCF file validated: 847 variants, GRCh38"),
    ("variant_filtering", "complete", "Filtered to 127 somatic coding variants (missense + frameshift + inframe)"),
    ("hla_assignment", "complete", "6 HLA alleles assigned from user input"),
    ("peptide_generation", "complete", "Generated 1,284 candidate 8-11mer peptides from 127 variants"),
    ("mhcflurry_prediction", "complete", "MHCflurry 2.0: scored 1,284 peptides x 6 alleles = 7,704 predictions"),
    ("immunogenicity_scoring", "complete", "Composite scoring complete. 312 peptides with IC50 < 500nM"),
    ("ranking", "complete", "Top 100 epitopes ranked by composite immunogenicity score"),
    ("results_stored", "complete", "Results written to database. Analysis complete."),
]


async def seed():
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        try:
            # Check if seed data already exists
            from sqlalchemy import select, func
            user_count = (await session.execute(select(func.count(User.id)))).scalar()
            if user_count > 0:
                print("Database already has data. Skipping seed.")
                return

            # 1. Create test user
            print("Step 1/8: Creating user...")
            user = User(
                email="demo@cvdash.dev",
                name="Demo Researcher",
                institution="University of Bristol",
                hashed_password=hash_password("Password1"),
                terms_accepted_at=datetime.now(timezone.utc),
            )
            session.add(user)
            await session.flush()
            print(f"  -> {user.email} (id={user.id})")

            # 2. Create project
            print("Step 2/8: Creating project...")
            project = Project(
                user_id=user.id,
                name="Melanoma Patient MEL-001",
                cancer_type="Cutaneous melanoma",
                stage="III",
                reference_genome="GRCh38",
            )
            session.add(project)
            await session.flush()
            print(f"  -> {project.name} (id={project.id})")

            # 3. Create completed analysis
            print("Step 3/8: Creating analysis...")
            started = datetime.now(timezone.utc) - timedelta(minutes=12)
            completed = datetime.now(timezone.utc) - timedelta(minutes=2)
            analysis = Analysis(
                project_id=project.id,
                status="complete",
                input_type="vcf",
                hla_provided=True,
                created_at=started,
                completed_at=completed,
            )
            session.add(analysis)
            await session.flush()
            print(f"  -> id={analysis.id}, status=complete")

            # 4. Create analysis input record
            print("Step 4/8: Creating input file record...")
            input_file = AnalysisInput(
                analysis_id=analysis.id,
                file_type="vcf",
                file_path="/data/uploads/MEL-001/somatic.vcf.gz",
                file_size=2_450_000,
                checksum="sha256:a1b2c3d4e5f6...",
            )
            session.add(input_file)

            # 5. Create HLA types
            print("Step 5/8: Creating HLA alleles...")
            for allele, source in HLA_ALLELES:
                session.add(HLAType(
                    analysis_id=analysis.id,
                    allele=allele,
                    source=source,
                ))
            print(f"  -> {len(HLA_ALLELES)} alleles")

            # 6. Create variants
            print("Step 6/8: Creating variants...")
            variant_objects = []
            for v in VARIANTS:
                variant = Variant(analysis_id=analysis.id, **v)
                session.add(variant)
                variant_objects.append(variant)
            await session.flush()
            print(f"  -> {len(VARIANTS)} variants")

            # 7. Create epitopes (sorted by score descending, rank 1 = best)
            print("Step 7/8: Creating epitopes...")
            sorted_epitopes = sorted(EPITOPES, key=lambda e: e["score"], reverse=True)
            for rank, ep in enumerate(sorted_epitopes, start=1):
                variant = variant_objects[ep["variant_idx"]]
                epitope = Epitope(
                    analysis_id=analysis.id,
                    variant_id=variant.id,
                    peptide_seq=ep["peptide_seq"],
                    peptide_length=ep["length"],
                    hla_allele=ep["hla"],
                    binding_affinity_nm=ep["affinity"],
                    presentation_score=ep["presentation"],
                    processing_score=ep["processing"],
                    expression_tpm=ep["expression"],
                    immunogenicity_score=ep["score"],
                    rank=rank,
                    explanation_json={
                        "binding_contribution": round(ep["presentation"] * 0.4, 3),
                        "expression_contribution": round((ep["expression"] / 100) * 0.25, 3),
                        "processing_contribution": round(ep["processing"] * 0.2, 3),
                        "vaf_contribution": round(VARIANTS[ep["variant_idx"]]["vaf"] * 0.15, 3),
                    },
                )
                session.add(epitope)
            print(f"  -> {len(sorted_epitopes)} epitopes (ranked 1-{len(sorted_epitopes)})")

            # 8. Create job logs with timestamps spaced across the analysis duration
            print("Step 8/8: Creating job logs...")
            step_duration = (completed - started) / len(PIPELINE_STEPS)
            for i, (step, step_status, message) in enumerate(PIPELINE_STEPS):
                log = JobLog(
                    analysis_id=analysis.id,
                    step=step,
                    status=step_status,
                    message=message,
                    timestamp=started + step_duration * (i + 1),
                )
                session.add(log)
            print(f"  -> {len(PIPELINE_STEPS)} log entries")

            await session.commit()
            print("\nSeed complete.")

        except Exception as e:
            await session.rollback()
            print(f"\nSeed FAILED at current step: {e}")
            raise

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
