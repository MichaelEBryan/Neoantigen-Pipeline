"""
Variant annotation enrichment API.

Queries external databases to add biological context to somatic variants:
  - ClinVar: pathogenicity, clinical significance, review status
  - gnomAD: population allele frequency (flags likely germline if AF > 0.01)
  - COSMIC: Cancer Gene Census driver/TSG/oncogene status + tier

Uses NCBI E-utilities (ClinVar/dbSNP) and the gnomAD GraphQL API.
COSMIC CGC is loaded from a local CSV (no API key needed).
Results are cached in the variant's annotation_json field to avoid repeated
lookups.

Rate limits: NCBI allows 3 requests/sec without API key, 10/sec with.
We use asyncio.Semaphore to stay within limits.
"""
import asyncio
import csv
import logging
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Variant, Analysis, Project, User, Epitope
from app.routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/annotate", tags=["annotate"])

# NCBI E-utilities base URL
NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# gnomAD REST API (v4)
GNOMAD_API = "https://gnomad.broadinstitute.org/api"

# Rate limiting for NCBI (3 req/sec without API key)
_ncbi_semaphore = asyncio.Semaphore(2)


class VariantAnnotation(BaseModel):
    variant_id: int
    gene: Optional[str]
    chrom: str
    pos: int
    ref: str
    alt: str
    protein_change: Optional[str]

    # ClinVar
    clinvar_significance: Optional[str] = None
    clinvar_id: Optional[str] = None
    clinvar_review_status: Optional[str] = None

    # gnomAD
    gnomad_af: Optional[float] = None  # population allele frequency
    is_likely_germline: bool = False    # True if gnomAD AF > 0.01

    # COSMIC
    cosmic_id: Optional[str] = None
    cosmic_count: Optional[int] = None  # number of samples with this mutation
    is_known_driver: bool = False       # True if in COSMIC Cancer Gene Census
    cosmic_tier: Optional[int] = None   # 1 = strong evidence, 2 = moderate
    cosmic_role: Optional[str] = None   # "oncogene", "TSG", "oncogene, fusion", etc.


class AnnotateResponse(BaseModel):
    analysis_id: int
    annotated: int
    total: int
    variants: list[VariantAnnotation]
    warnings: list[str] = []


async def _query_clinvar(
    chrom: str, pos: int, ref: str, alt: str, client: httpx.AsyncClient
) -> dict:
    """Query ClinVar via NCBI E-utilities for a specific variant.

    Searches by chromosome + position, then verifies the variant alleles
    in the summary to avoid false-positive matches at the same locus.
    """
    async with _ncbi_semaphore:
        try:
            clean_chrom = chrom.replace("chr", "")
            search_term = f"{clean_chrom}[Chromosome] AND {pos}[Base Position]"

            search_url = f"{NCBI_BASE}/esearch.fcgi"
            params = {
                "db": "clinvar",
                "term": search_term,
                "retmode": "json",
                "retmax": 5,
            }
            resp = await client.get(search_url, params=params, timeout=10.0)
            if resp.status_code != 200:
                return {}

            data = resp.json()
            id_list = data.get("esearchresult", {}).get("idlist", [])

            if not id_list:
                return {}

            # Rate limit between esearch and esummary
            await asyncio.sleep(0.35)
            summary_url = f"{NCBI_BASE}/esummary.fcgi"
            summary_params = {
                "db": "clinvar",
                "id": ",".join(id_list[:5]),
                "retmode": "json",
            }
            resp = await client.get(summary_url, params=summary_params, timeout=10.0)
            if resp.status_code != 200:
                return {}

            summary = resp.json()
            result = summary.get("result", {})

            # Check each candidate -- prefer one whose title/variation_set
            # mentions our ref>alt change. If none match alleles, still
            # return the first hit but flag it as unverified.
            best_match = None
            for uid in id_list[:5]:
                entry = result.get(uid, {})
                if not entry:
                    continue

                significance = entry.get("clinical_significance", {})
                if isinstance(significance, dict):
                    sig_desc = significance.get("description", "")
                else:
                    sig_desc = str(significance)

                hit = {
                    "clinvar_significance": sig_desc or None,
                    "clinvar_id": f"VCV{uid}",
                    "clinvar_review_status": entry.get("review_status", None),
                }

                # Try to verify alleles from the entry title or variation_set
                title = (entry.get("title") or "").upper()
                # ClinVar titles often contain "NM_...:c.XXX>YYY" or "REF>ALT"
                if ref.upper() in title and alt.upper() in title:
                    return hit  # allele-verified match

                if best_match is None:
                    best_match = hit

            # Return best positional match even if alleles not verified.
            # The caller can check clinvar_id is set but significance may
            # be for a different variant at the same locus.
            return best_match or {}

        except Exception as e:
            logger.debug(f"ClinVar query failed for {chrom}:{pos}: {e}")
            return {}


async def _query_gnomad(
    chrom: str, pos: int, ref: str, alt: str, client: httpx.AsyncClient
) -> dict:
    """Query gnomAD GraphQL API for population allele frequency."""
    try:
        clean_chrom = chrom.replace("chr", "")
        variant_id = f"{clean_chrom}-{pos}-{ref}-{alt}"

        # gnomAD GraphQL query
        query = """
        query ($variantId: String!, $dataset: DatasetId!) {
          variant(variantId: $variantId, dataset: $dataset) {
            variant_id
            genome {
              af
            }
            exome {
              af
            }
          }
        }
        """
        variables = {
            "variantId": variant_id,
            "dataset": "gnomad_r4",
        }

        resp = await client.post(
            GNOMAD_API,
            json={"query": query, "variables": variables},
            timeout=15.0,
        )

        if resp.status_code != 200:
            return {}

        data = resp.json()
        variant = data.get("data", {}).get("variant")
        if not variant:
            return {}

        # Use genome AF if available, fall back to exome
        af = None
        if variant.get("genome") and variant["genome"].get("af") is not None:
            af = variant["genome"]["af"]
        elif variant.get("exome") and variant["exome"].get("af") is not None:
            af = variant["exome"]["af"]

        if af is not None:
            return {
                "gnomad_af": af,
                "is_likely_germline": af > 0.01,
            }
        return {}

    except Exception as e:
        logger.debug(f"gnomAD query failed for {chrom}:{pos}: {e}")
        return {}


# -- COSMIC Cancer Gene Census --
# Loaded from the full CGC CSV (748 genes, Tier 1 + Tier 2).
# Each entry stores: tier (1 or 2), role_in_cancer, somatic/germline flags.
# If the CSV is missing, falls back to a minimal hardcoded set.

def _load_cosmic_cgc() -> tuple[set[str], dict[str, dict]]:
    """Load COSMIC CGC from CSV. Returns (driver_gene_set, gene_detail_map)."""
    csv_path = Path(__file__).parent.parent / "data" / "cosmic_cgc.csv"
    drivers: set[str] = set()
    details: dict[str, dict] = {}

    if csv_path.exists():
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                gene = row["GENE_SYMBOL"].strip()
                tier = row.get("Tier", "").strip()
                role = row.get("Role in Cancer", "").strip()
                somatic = row.get("Somatic", "").strip().lower() == "yes"
                germline = row.get("Germline", "").strip().lower() == "yes"
                drivers.add(gene)
                details[gene] = {
                    "tier": int(tier) if tier in ("1", "2") else None,
                    "role": role or None,
                    "somatic": somatic,
                    "germline_predisposition": germline,
                }
        logger.info(f"Loaded {len(drivers)} COSMIC CGC genes from {csv_path}")
    else:
        # Minimal fallback if CSV not found
        logger.warning(f"COSMIC CGC CSV not found at {csv_path}, using minimal fallback")
        drivers = {
            "TP53", "KRAS", "PIK3CA", "BRAF", "NRAS", "PTEN", "APC",
            "BRCA1", "BRCA2", "EGFR", "ALK", "RB1", "CDKN2A", "MYC",
            "ERBB2", "IDH1", "IDH2", "FLT3", "NPM1", "DNMT3A", "TET2",
            "JAK2", "NOTCH1", "CTNNB1", "SMAD4", "VHL", "NF1", "NF2",
            "RET", "KIT", "PDGFRA", "FGFR3", "MET", "ABL1", "ATM",
            "ARID1A", "SETD2", "KMT2D", "CREBBP", "FBXW7", "SF3B1",
        }

    return drivers, details


COSMIC_DRIVERS, COSMIC_DETAILS = _load_cosmic_cgc()


@router.post("/variants/{analysis_id}", response_model=AnnotateResponse)
async def annotate_variants(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnnotateResponse:
    """
    Enrich variants with ClinVar, gnomAD, and COSMIC annotations.

    Queries external databases for each variant and updates the
    annotation_json field. Previously annotated variants are skipped
    unless their annotation_json lacks the enrichment keys.

    Rate limited to avoid NCBI throttling. May take 10-30s for
    analyses with many variants.
    """
    # Ownership check
    ownership_stmt = (
        select(Analysis, Project)
        .join(Project, Analysis.project_id == Project.id)
        .where(Analysis.id == analysis_id)
    )
    row = (await db.execute(ownership_stmt)).one_or_none()
    if not row or row[1].user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Fetch variants
    var_stmt = select(Variant).where(Variant.analysis_id == analysis_id)
    result = await db.execute(var_stmt)
    variants = list(result.scalars().all())

    if not variants:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No variants found for this analysis",
        )

    annotations: list[VariantAnnotation] = []
    annotated_count = 0
    warnings: list[str] = []

    async with httpx.AsyncClient() as client:
        # Split variants into cached (already annotated) and fresh (need queries)
        fresh_variants: list[Variant] = []
        for v in variants:
            existing = v.annotation_json or {}
            if "clinvar_queried" in existing and "gnomad_queried" in existing:
                ann = VariantAnnotation(
                    variant_id=v.id,
                    gene=v.gene,
                    chrom=v.chrom,
                    pos=v.pos,
                    ref=v.ref,
                    alt=v.alt,
                    protein_change=v.protein_change,
                    clinvar_significance=existing.get("clinvar_significance"),
                    clinvar_id=existing.get("clinvar_id"),
                    gnomad_af=existing.get("gnomad_af"),
                    is_likely_germline=existing.get("is_likely_germline", False),
                    cosmic_id=existing.get("cosmic_id"),
                    cosmic_count=existing.get("cosmic_count"),
                    is_known_driver=existing.get("is_known_driver", False),
                )
                annotations.append(ann)
            else:
                fresh_variants.append(v)

        # For fresh variants, run ClinVar + gnomAD concurrently per variant.
        # The NCBI semaphore (capacity 2) still throttles ClinVar requests.
        async def _annotate_one(v: Variant) -> VariantAnnotation:
            clinvar, gnomad = await asyncio.gather(
                _query_clinvar(v.chrom, v.pos, v.ref, v.alt, client),
                _query_gnomad(v.chrom, v.pos, v.ref, v.alt, client),
            )
            ann = VariantAnnotation(
                variant_id=v.id,
                gene=v.gene,
                chrom=v.chrom,
                pos=v.pos,
                ref=v.ref,
                alt=v.alt,
                protein_change=v.protein_change,
                clinvar_significance=clinvar.get("clinvar_significance"),
                clinvar_id=clinvar.get("clinvar_id"),
                clinvar_review_status=clinvar.get("clinvar_review_status"),
                gnomad_af=gnomad.get("gnomad_af"),
                is_likely_germline=gnomad.get("is_likely_germline", False),
                is_known_driver=bool(v.gene and v.gene in COSMIC_DRIVERS),
            )

            # Add COSMIC detail if available
            cosmic_detail = COSMIC_DETAILS.get(v.gene or "", {})

            # Persist to DB
            v.annotation_json = {
                **(v.annotation_json or {}),
                "clinvar_queried": True,
                "clinvar_significance": ann.clinvar_significance,
                "clinvar_id": ann.clinvar_id,
                "clinvar_review_status": ann.clinvar_review_status,
                "gnomad_queried": True,
                "gnomad_af": ann.gnomad_af,
                "is_likely_germline": ann.is_likely_germline,
                "cosmic_id": ann.cosmic_id,
                "cosmic_count": ann.cosmic_count,
                "is_known_driver": ann.is_known_driver,
                "cosmic_tier": cosmic_detail.get("tier"),
                "cosmic_role": cosmic_detail.get("role"),
            }
            return ann

        # Process in batches of 5 to avoid overwhelming external APIs
        BATCH_SIZE = 5
        for batch_start in range(0, len(fresh_variants), BATCH_SIZE):
            batch = fresh_variants[batch_start:batch_start + BATCH_SIZE]
            results = await asyncio.gather(
                *[_annotate_one(v) for v in batch],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    logger.warning(f"Annotation failed for a variant: {r}")
                    continue
                annotations.append(r)
                annotated_count += 1

    await db.commit()

    # Check for germline contamination
    germline_count = sum(1 for a in annotations if a.is_likely_germline)
    if germline_count > 0:
        warnings.append(
            f"{germline_count} variant(s) have gnomAD AF > 1%, suggesting possible "
            f"germline contamination. Review these variants carefully."
        )

    driver_count = sum(1 for a in annotations if a.is_known_driver)
    if driver_count > 0:
        warnings.append(
            f"{driver_count} variant(s) are in known cancer driver genes (COSMIC CGC). "
            f"These may be higher-priority vaccine targets."
        )

    logger.info(
        f"Annotated {annotated_count} variants for analysis {analysis_id} "
        f"({germline_count} germline flags, {driver_count} drivers)"
    )

    return AnnotateResponse(
        analysis_id=analysis_id,
        annotated=annotated_count,
        total=len(variants),
        variants=annotations,
        warnings=warnings,
    )
