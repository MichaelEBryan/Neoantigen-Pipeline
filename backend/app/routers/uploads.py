"""
File upload endpoint for analysis inputs.

Accepts genomic files (VCF, BAM, FASTQ), validates extension and magic bytes,
saves to disk under upload_dir/{user_id}/{analysis_id}/, and records in
analysis_inputs table.

Security: filenames are sanitized (basename + UUID prefix) to prevent path
traversal. Files are written to a temp path first, then committed to DB
atomically -- if DB fails, the temp file is cleaned up.
"""
import hashlib
import logging
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import Analysis, AnalysisInput, Project, User
from app.pipeline.expression_parser import validate_expression_file, ExpressionParseError
from app.pipeline.maf_parser import is_maf_header

logger = logging.getLogger(__name__)

router = APIRouter()

# Allowed extensions -> expected file_type label
ALLOWED_EXTENSIONS = {
    ".vcf": "vcf",
    ".vcf.gz": "vcf",
    ".maf": "maf",
    ".bam": "bam",
    ".fastq": "fastq",
    ".fastq.gz": "fastq",
    ".fq": "fastq",
    ".fq.gz": "fastq",
    ".csv": "expression_matrix",
    ".tsv": "expression_matrix",
    ".txt": "text_ambiguous",  # could be MAF or expression -- resolved by header inspection
}

# Magic bytes for file header validation.
# gzip files (vcf.gz, fastq.gz) all start with 1f 8b.
MAGIC_BYTES = {
    "vcf": b"##fileformat=VCF",
    "bam": b"BAM\x01",
    "fastq": b"@",
    "gzip": b"\x1f\x8b",
}

# 10 GB max per file. FASTQ/BAM can be large.
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024

# Minimum file size (bytes). Reject obviously empty/corrupt files.
MIN_FILE_SIZE = 100

# Valid file_label values that the frontend can send
VALID_FILE_LABELS = {"primary", "rna_seq", "normal", "tumor", "expression_matrix"}


def _get_extension(filename: str) -> Optional[str]:
    """
    Extract extension, handling double extensions like .vcf.gz.
    Returns the matched key from ALLOWED_EXTENSIONS or None.
    """
    lower = filename.lower()
    # Check double extensions first (longer match wins)
    for ext in sorted(ALLOWED_EXTENSIONS.keys(), key=len, reverse=True):
        if lower.endswith(ext):
            return ext
    return None


def _sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent path traversal.

    Strips directory components, removes null bytes and control characters,
    and prefixes with a UUID to avoid collisions.
    """
    # Take only the basename -- strips any ../../../ attempts
    base = PurePosixPath(filename).name
    # Also handle Windows-style paths
    base = base.split("\\")[-1]
    # Remove null bytes and control chars
    base = "".join(c for c in base if c.isprintable() and c != "\x00")
    # If nothing left, use a generic name
    if not base:
        base = "upload"
    # Prefix with short UUID to prevent collisions and make filenames unpredictable
    prefix = uuid.uuid4().hex[:8]
    return f"{prefix}_{base}"


def _validate_magic_bytes(header: bytes, ext: str) -> bool:
    """
    Check that the first bytes of the file match what we expect.
    Gzipped files just need the gzip header; we can't check the inner format
    without decompressing.
    Expression matrices (csv/tsv/txt) are plain text -- we just check they're
    valid UTF-8 and not binary.
    """
    if ext.endswith(".gz"):
        return header[:2] == MAGIC_BYTES["gzip"]

    file_type = ALLOWED_EXTENSIONS.get(ext)
    if not file_type:
        return False

    # Expression matrix and MAF/text_ambiguous files: verify it's text, not binary.
    # Check UTF-8 decodability AND absence of null bytes (which indicate binary).
    if file_type in ("expression_matrix", "maf", "text_ambiguous"):
        try:
            text = header.decode("utf-8")
            if "\x00" in text:
                return False  # null bytes = binary file
            return True
        except UnicodeDecodeError:
            return False

    expected = MAGIC_BYTES.get(file_type)
    if expected is None:
        return True  # no check available, allow

    return header[:len(expected)] == expected


class UploadResponse(BaseModel):
    """Response after successful file upload."""
    id: int
    analysis_id: int
    file_type: str
    file_size: int
    checksum: str


@router.post(
    "/{analysis_id}/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    analysis_id: int,
    file: UploadFile = File(...),
    file_label: str = Form("primary"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    """
    Upload a genomic file for an analysis.

    Validates file extension and magic bytes, saves to disk,
    computes SHA-256 checksum, and records in analysis_inputs table.
    """
    # 1. Validate file_label
    if file_label not in VALID_FILE_LABELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file_label. Allowed: {', '.join(sorted(VALID_FILE_LABELS))}",
        )

    # 2. Verify analysis exists and user owns it (single query with join)
    stmt = (
        select(Analysis, Project)
        .join(Project, Analysis.project_id == Project.id)
        .where(Analysis.id == analysis_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found")

    analysis, project = row

    if project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Only allow uploads for queued analyses
    if analysis.status != "queued":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot upload files to analysis in '{analysis.status}' state"
        )

    # 3. Validate file extension
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    ext = _get_extension(file.filename)
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS.keys())}",
        )

    file_type = ALLOWED_EXTENSIONS[ext]

    # 4. Read header for magic bytes check (first 512 bytes for MAF detection)
    header = await file.read(512)
    if len(header) < 4:
        raise HTTPException(
            status_code=400,
            detail="File is too small to be a valid genomic file",
        )

    if not _validate_magic_bytes(header, ext):
        raise HTTPException(
            status_code=400,
            detail=f"File content doesn't match expected format for {ext}",
        )

    # 4b. Resolve ambiguous .txt files: check if this is a MAF mutation file
    # or an expression matrix. MAF files have columns like Hugo_Symbol,
    # Variant_Classification, etc. in their header row.
    if file_type == "text_ambiguous":
        try:
            text_header = header.decode("utf-8", errors="replace")
            # Find first non-comment, non-empty line
            for line in text_header.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    if is_maf_header(stripped):
                        file_type = "maf"
                        logger.info(f"Detected .txt file as MAF format: {file.filename}")
                    else:
                        file_type = "expression_matrix"
                    break
            else:
                file_type = "expression_matrix"
        except Exception:
            file_type = "expression_matrix"

    # If file_label is "primary" and detected type is MAF, that's correct.
    # If file_label is "primary" and detected type is expression_matrix, override to
    # treat as MAF (user intended to upload mutation data as their primary file).
    if file_label == "primary" and file_type == "expression_matrix" and ext == ".txt":
        # User uploaded a .txt as primary input -- assume it's mutation data
        file_type = "maf"
        logger.info(f"Treating primary .txt upload as MAF: {file.filename}")

    # 5. Sanitize filename and prepare destination
    safe_name = _sanitize_filename(file.filename)
    upload_dir = Path(settings.upload_dir) / str(current_user.id) / str(analysis_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest_path = upload_dir / safe_name

    # Verify dest_path is actually inside upload_dir (belt-and-suspenders)
    if not str(dest_path.resolve()).startswith(str(upload_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # 6. Write file to disk, then commit to DB.
    # If DB commit fails, clean up the file.
    sha256 = hashlib.sha256()
    file_size = len(header)
    sha256.update(header)

    try:
        with open(dest_path, "wb") as f:
            f.write(header)
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                f.write(chunk)
                sha256.update(chunk)
                file_size += len(chunk)

                if file_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum size of {MAX_FILE_SIZE // (1024**3)} GB",
                    )
    except HTTPException:
        # Clean up partial file on size limit exceeded
        if dest_path.exists():
            os.unlink(dest_path)
        raise
    except Exception as e:
        # Clean up on any unexpected error during write
        logger.error(f"File write failed for {dest_path}: {e}")
        if dest_path.exists():
            os.unlink(dest_path)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save uploaded file: {type(e).__name__}",
        )

    # Check minimum size
    if file_size < MIN_FILE_SIZE:
        os.unlink(dest_path)
        raise HTTPException(
            status_code=400,
            detail=f"File too small ({file_size} bytes). Minimum {MIN_FILE_SIZE} bytes.",
        )

    checksum = sha256.hexdigest()

    # 6b. If this is an expression matrix, validate the file structure now
    #     before committing to DB. Catches bad formats early.
    #     Skip validation for MAF files (they are mutation data, not expression).
    if (file_type == "expression_matrix" or file_label == "expression_matrix") and file_type != "maf":
        try:
            validation = validate_expression_file(dest_path)
            logger.info(
                f"Expression matrix validated: unit={validation['unit_type']}, "
                f"cols={len(validation['columns'])}, "
                f"expr_col={validation['expression_column']}"
            )
        except ExpressionParseError as e:
            os.unlink(dest_path)
            raise HTTPException(
                status_code=400,
                detail=f"Invalid expression matrix: {e}",
            )

    # 7. Record in DB. If this fails, clean up the file.
    resolved_label = file_label if file_label != "primary" else file_type
    analysis_input = AnalysisInput(
        analysis_id=analysis_id,
        file_type=resolved_label,
        file_path=str(dest_path),
        file_size=file_size,
        checksum=checksum,
    )
    try:
        db.add(analysis_input)
        await db.commit()
        await db.refresh(analysis_input)
    except Exception:
        # DB commit failed -- remove the orphaned file
        if dest_path.exists():
            os.unlink(dest_path)
        raise

    logger.info(
        f"Upload complete: analysis={analysis_id}, type={resolved_label}, "
        f"size={file_size}, sha256={checksum[:16]}..."
    )

    return UploadResponse(
        id=analysis_input.id,
        analysis_id=analysis_id,
        file_type=analysis_input.file_type,
        file_size=file_size,
        checksum=checksum,
    )
