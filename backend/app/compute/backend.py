"""
Compute backend protocol and factory.

Defines the interface that both GCP Batch and Isambard implementations
must satisfy. The rest of the codebase only depends on ComputeBackend,
never on a concrete implementation.

Usage:
    backend = get_compute_backend()           # reads config
    job_id = await backend.submit(request)
    status = await backend.poll(job_id)
    vcf = await backend.retrieve_vcf(job_id)
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------

class JobState(str, enum.Enum):
    """Normalised job states across all backends."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED)


@dataclass
class SubmitRequest:
    """Everything the compute backend needs to launch a variant-calling job.

    Agnostic to the backend. The backend decides how to translate these
    into container args, Slurm directives, etc.
    """
    analysis_id: int
    # Local paths to input files (backend uploads them to remote storage)
    input_files: list[str]               # FASTQ or BAM paths
    input_type: str                       # "fastq" | "bam"
    hla_alleles: list[str]               # e.g. ["HLA-A*02:01", ...]
    reference_genome: str = "GRCh38"     # GRCh37 or GRCh38
    tumor_normal_paired: bool = False
    # Optional overrides -- backends can ignore if unsupported
    cpu: int = 16
    memory_gb: int = 32
    gpu: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class JobStatus:
    """Normalised status returned by poll()."""
    job_id: str
    state: JobState
    progress_pct: float = 0.0           # 0.0 - 1.0
    message: str = ""
    # Backend-specific metadata (Slurm job ID, GCP operation name, etc.)
    backend_meta: dict = field(default_factory=dict)


@dataclass
class JobResult:
    """Returned by retrieve_results() after a job succeeds."""
    job_id: str
    vcf_local_path: str                 # local path to downloaded VCF
    # Any extra outputs (BAM index, QC reports, etc.)
    extra_files: dict[str, str] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol -- the only thing the rest of the codebase depends on
# ---------------------------------------------------------------------------

@runtime_checkable
class ComputeBackend(Protocol):
    """Abstract interface for remote compute dispatch.

    Every method is async so callers can await without blocking
    the event loop. Sync backends should wrap blocking calls with
    asyncio.to_thread.
    """

    @property
    def name(self) -> str:
        """Human-readable backend name, e.g. 'gcp-batch' or 'isambard'."""
        ...

    async def submit(self, request: SubmitRequest) -> str:
        """Upload inputs and submit the variant-calling job.

        Returns a backend-specific job ID string.
        Raises ComputeError on failure.
        """
        ...

    async def poll(self, job_id: str) -> JobStatus:
        """Check current status of a submitted job.

        Safe to call frequently -- backends should handle rate limits
        internally.
        """
        ...

    async def cancel(self, job_id: str) -> bool:
        """Request cancellation. Returns True if the cancel was accepted."""
        ...

    async def retrieve_results(self, job_id: str, dest_dir: str) -> JobResult:
        """Download output VCF (and any extras) to dest_dir.

        Only valid after poll() returns SUCCEEDED.
        Raises ComputeError if job hasn't succeeded or download fails.
        """
        ...

    async def cleanup(self, job_id: str) -> None:
        """Delete remote resources (uploaded inputs, intermediate files).

        Best-effort -- should not raise on failure.
        """
        ...


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ComputeError(Exception):
    """Base exception for compute backend failures."""

    def __init__(self, message: str, backend: str = "", job_id: str = ""):
        self.backend = backend
        self.job_id = job_id
        super().__init__(message)


class SubmitError(ComputeError):
    """Job submission failed (bad config, quota exceeded, auth error)."""
    pass


class PollError(ComputeError):
    """Failed to check job status (network, auth, not found)."""
    pass


class RetrieveError(ComputeError):
    """Failed to download results."""
    pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_compute_backend(backend_name: str | None = None) -> ComputeBackend:
    """Create a compute backend instance.

    Args:
        backend_name: "gcp-batch", "isambard", or None (reads from config).
    """
    from app.config import settings

    name = backend_name or getattr(settings, "compute_backend", "gcp-batch")

    if name == "gcp-batch":
        from app.compute.gcp_batch import GCPBatchBackend
        return GCPBatchBackend()
    elif name == "isambard":
        from app.compute.isambard import IsambardBackend
        return IsambardBackend()
    else:
        raise ValueError(
            f"Unknown compute backend: {name!r}. "
            "Expected 'gcp-batch' or 'isambard'."
        )
