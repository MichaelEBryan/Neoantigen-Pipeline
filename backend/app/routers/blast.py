"""
BLAST self-similarity check for neoepitope candidates.

Queries NCBI BLAST REST API (blastp against refseq_protein, organism=human)
to identify whether predicted neoepitopes are similar to self-peptides.
Peptides with high identity to human proteins are at risk of T-cell tolerance
and may be poor vaccine candidates.

This is optional and off by default. Users can enable it from the results page.
"""
import logging
import asyncio
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Epitope, Analysis, Project, User
from app.routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/blast", tags=["blast"])

NCBI_BLAST_URL = "https://blast.ncbi.nlm.nih.gov/blast/Blast.cgi"

# Timeout for NCBI BLAST queries. These are remote and can be slow.
BLAST_TIMEOUT = 120  # seconds


class BlastRequest(BaseModel):
    analysis_id: int
    epitope_ids: list[int] = Field(..., max_length=50)


class BlastHit(BaseModel):
    epitope_id: int
    peptide_seq: str
    hit_accession: Optional[str] = None
    hit_title: Optional[str] = None
    identity_pct: float  # 0-100
    alignment_length: int
    evalue: float
    is_self_hit: bool  # True if >= 100% identity to a human protein


class BlastResult(BaseModel):
    epitope_id: int
    peptide_seq: str
    hits: list[BlastHit]
    is_self_similar: bool  # True if any hit has >=80% identity
    max_identity_pct: float
    status: str  # "complete", "no_hits", "error", "timeout"
    error_message: Optional[str] = None


class BlastResponse(BaseModel):
    results: list[BlastResult]
    total_checked: int
    self_similar_count: int  # how many peptides are self-similar


async def _blast_peptide(peptide: str, timeout: int = BLAST_TIMEOUT) -> dict:
    """
    Run a single BLAST query against NCBI for one peptide.
    Uses the NCBI BLAST REST API with blastp against human refseq_protein.

    Returns a dict with hits or error info.
    """
    # For very short peptides (8-11 AA), use blastp-short
    program = "blastp-short" if len(peptide) <= 30 else "blastp"

    put_params = {
        "CMD": "Put",
        "PROGRAM": program,
        "DATABASE": "refseq_protein",
        "QUERY": peptide,
        "ENTREZ_QUERY": "Homo sapiens[ORGN]",
        "EXPECT": "10",
        "WORD_SIZE": "2" if len(peptide) <= 15 else "3",
        "MATRIX_NAME": "PAM30" if len(peptide) <= 15 else "BLOSUM62",
        "FORMAT_TYPE": "JSON2",
        "HITLIST_SIZE": "5",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Submit the BLAST job
            put_resp = await client.post(NCBI_BLAST_URL, data=put_params)
            put_resp.raise_for_status()
            put_text = put_resp.text

            # Extract RID (Request ID) from response
            rid = None
            for line in put_text.split("\n"):
                if line.strip().startswith("RID = "):
                    rid = line.strip().split("=")[1].strip()
                    break

            if not rid:
                return {"status": "error", "error": "No RID returned from NCBI BLAST"}

            # Poll for results (BLAST jobs take 10-60s typically)
            for attempt in range(30):
                await asyncio.sleep(4)

                check_params = {
                    "CMD": "Get",
                    "RID": rid,
                    "FORMAT_TYPE": "JSON2",
                    "FORMAT_OBJECT": "SearchInfo",
                }
                check_resp = await client.get(NCBI_BLAST_URL, params=check_params)
                check_text = check_resp.text

                if "Status=WAITING" in check_text:
                    continue
                elif "Status=FAILED" in check_text:
                    return {"status": "error", "error": "NCBI BLAST job failed"}
                elif "Status=READY" in check_text:
                    # Fetch actual results
                    get_params = {
                        "CMD": "Get",
                        "RID": rid,
                        "FORMAT_TYPE": "JSON2",
                    }
                    result_resp = await client.get(NCBI_BLAST_URL, params=get_params)
                    try:
                        result_json = result_resp.json()
                        return {"status": "complete", "data": result_json}
                    except Exception:
                        return {"status": "complete", "data": result_resp.text}

            return {"status": "timeout", "error": "BLAST query timed out after 2 minutes"}

    except httpx.TimeoutException:
        return {"status": "timeout", "error": "HTTP timeout connecting to NCBI"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _parse_blast_json(result_data: dict, epitope_id: int, peptide: str) -> BlastResult:
    """Parse NCBI BLAST JSON2 format into our BlastResult model."""
    hits = []
    try:
        # Navigate the NCBI JSON2 structure
        search = result_data.get("BlastOutput2", [{}])
        if isinstance(search, list) and len(search) > 0:
            report = search[0].get("report", {})
            search_results = report.get("results", {}).get("search", {})
            blast_hits = search_results.get("hits", [])

            for h in blast_hits[:5]:
                desc = h.get("description", [{}])[0] if h.get("description") else {}
                hsps = h.get("hsps", [{}])
                best_hsp = hsps[0] if hsps else {}

                identity = best_hsp.get("identity", 0)
                align_len = best_hsp.get("align_len", 1)
                identity_pct = (identity / align_len * 100) if align_len > 0 else 0
                evalue = best_hsp.get("evalue", 999)

                hits.append(BlastHit(
                    epitope_id=epitope_id,
                    peptide_seq=peptide,
                    hit_accession=desc.get("accession", ""),
                    hit_title=desc.get("title", "")[:200],
                    identity_pct=round(identity_pct, 1),
                    alignment_length=align_len,
                    evalue=evalue,
                    is_self_hit=identity_pct >= 100,
                ))
    except Exception as e:
        logger.warning(f"Failed to parse BLAST result for {peptide}: {e}")

    max_identity = max((h.identity_pct for h in hits), default=0)
    is_self = max_identity >= 80

    return BlastResult(
        epitope_id=epitope_id,
        peptide_seq=peptide,
        hits=hits,
        is_self_similar=is_self,
        max_identity_pct=max_identity,
        status="complete" if hits else "no_hits",
    )


@router.post("/check", response_model=BlastResponse)
async def blast_check(
    req: BlastRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BlastResponse:
    """
    Run BLAST self-similarity check on selected epitopes.

    Queries NCBI BLAST (blastp-short) against human refseq_protein.
    Flags peptides with >= 80% identity to known human proteins.
    """
    if not req.epitope_ids:
        raise HTTPException(status_code=400, detail="No epitope IDs provided")

    # Fetch epitopes and verify ownership
    stmt = select(Epitope).where(Epitope.id.in_(req.epitope_ids))
    result = await db.execute(stmt)
    epitopes = list(result.scalars().all())

    if not epitopes:
        raise HTTPException(status_code=404, detail="No epitopes found")

    # Ownership check via Analysis -> Project
    analysis_ids = set(e.analysis_id for e in epitopes)
    for aid in analysis_ids:
        own_stmt = (
            select(Analysis, Project)
            .join(Project, Analysis.project_id == Project.id)
            .where(Analysis.id == aid)
        )
        own_result = await db.execute(own_stmt)
        row = own_result.one_or_none()
        if not row or row[1].user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    # Run BLAST queries concurrently (max 5 at a time to be nice to NCBI)
    semaphore = asyncio.Semaphore(3)
    results: list[BlastResult] = []

    async def check_one(ep):
        async with semaphore:
            raw = await _blast_peptide(ep.peptide_seq)
            if raw["status"] == "complete" and isinstance(raw.get("data"), dict):
                return _parse_blast_json(raw["data"], ep.id, ep.peptide_seq)
            else:
                return BlastResult(
                    epitope_id=ep.id,
                    peptide_seq=ep.peptide_seq,
                    hits=[],
                    is_self_similar=False,
                    max_identity_pct=0,
                    status=raw.get("status", "error"),
                    error_message=raw.get("error"),
                )

    tasks = [check_one(ep) for ep in epitopes]
    results = await asyncio.gather(*tasks)

    self_similar_count = sum(1 for r in results if r.is_self_similar)

    return BlastResponse(
        results=results,
        total_checked=len(results),
        self_similar_count=self_similar_count,
    )
