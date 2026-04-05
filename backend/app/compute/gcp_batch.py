"""
Google Cloud Batch backend for variant calling.

Uploads FASTQ/BAM to a GCS bucket, submits a Batch job that runs
the variant-calling Nextflow pipeline inside a Docker container,
then downloads the resulting VCF.

Architecture:
    GCS bucket layout:
        gs://{bucket}/jobs/{analysis_id}/inputs/   -- uploaded FASTQ/BAM
        gs://{bucket}/jobs/{analysis_id}/outputs/   -- VCF + index + QC
        gs://{bucket}/jobs/{analysis_id}/work/      -- Nextflow scratch

    Batch job:
        Single taskGroup with 1 task. The task runs a Docker container
        that mounts the GCS paths and executes the Nextflow pipeline.
        Environment variables pass analysis-specific config (reference,
        HLA alleles, paired mode, etc.).

Requires:
    pip install google-cloud-batch google-cloud-storage

    Service account needs:
        - roles/batch.jobsEditor
        - roles/storage.objectAdmin (on the pipeline bucket)
        - roles/iam.serviceAccountUser (to act as the job's service account)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.compute.backend import (
    ComputeBackend,
    ComputeError,
    JobResult,
    JobState,
    JobStatus,
    PollError,
    RetrieveError,
    SubmitError,
    SubmitRequest,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping from GCP Batch states to our normalised JobState
# ---------------------------------------------------------------------------
_GCP_STATE_MAP: dict[str, JobState] = {
    "STATE_UNSPECIFIED": JobState.PENDING,
    "QUEUED": JobState.QUEUED,
    "SCHEDULED": JobState.QUEUED,
    "RUNNING": JobState.RUNNING,
    "SUCCEEDED": JobState.SUCCEEDED,
    "FAILED": JobState.FAILED,
    "DELETION_IN_PROGRESS": JobState.CANCELLED,
}


def _settings():
    """Lazy import to avoid circular dependency at module level."""
    from app.config import settings
    return settings


class GCPBatchBackend:
    """ComputeBackend implementation using Google Cloud Batch + GCS.

    All blocking I/O (GCS uploads, Batch API calls) is wrapped in
    asyncio.to_thread so we never block the event loop.
    """

    def __init__(self):
        s = _settings()
        self._project = s.gcp_project_id
        self._region = s.gcp_region
        self._bucket_name = s.gcp_pipeline_bucket
        self._service_account = s.gcp_service_account
        self._container_image = s.gcp_pipeline_image
        self._machine_type = s.gcp_machine_type
        self._boot_disk_gb = s.gcp_boot_disk_gb
        self._nextflow_profile = s.gcp_nextflow_profile

        # Lazy-init GCP clients (avoids import errors when GCP libs
        # aren't installed -- e.g. in unit tests or Isambard-only deploys)
        self._batch_client = None
        self._storage_client = None

    # -- Properties ---

    @property
    def name(self) -> str:
        return "gcp-batch"

    # -- Client init (lazy, thread-safe enough for our usage) ---

    def _get_batch_client(self):
        if self._batch_client is None:
            from google.cloud import batch_v1
            self._batch_client = batch_v1.BatchServiceClient()
        return self._batch_client

    def _get_storage_client(self):
        if self._storage_client is None:
            from google.cloud import storage
            self._storage_client = storage.Client(project=self._project)
        return self._storage_client

    def _bucket(self):
        return self._get_storage_client().bucket(self._bucket_name)

    # -- GCS helpers (sync, run via to_thread) ---

    def _upload_file(self, local_path: str, gcs_prefix: str) -> str:
        """Upload a single file to GCS. Returns gs:// URI."""
        fname = Path(local_path).name
        blob_path = f"{gcs_prefix}/{fname}"
        blob = self._bucket().blob(blob_path)

        logger.info(f"Uploading {local_path} -> gs://{self._bucket_name}/{blob_path}")
        blob.upload_from_filename(local_path, timeout=1800)  # 30 min for large BAMs
        return f"gs://{self._bucket_name}/{blob_path}"

    def _download_blob(self, blob_path: str, local_dest: str) -> str:
        """Download a GCS blob to a local file. Returns the local path."""
        blob = self._bucket().blob(blob_path)
        os.makedirs(os.path.dirname(local_dest), exist_ok=True)

        logger.info(f"Downloading gs://{self._bucket_name}/{blob_path} -> {local_dest}")
        blob.download_to_filename(local_dest, timeout=600)
        return local_dest

    def _list_blobs(self, prefix: str) -> list[str]:
        """List blob names under a prefix."""
        return [b.name for b in self._bucket().list_blobs(prefix=prefix)]

    def _delete_prefix(self, prefix: str) -> int:
        """Delete all blobs under a prefix. Returns count deleted."""
        blobs = list(self._bucket().list_blobs(prefix=prefix))
        if not blobs:
            return 0
        self._bucket().delete_blobs(blobs)
        return len(blobs)

    # -- Batch job definition builder ---

    def _build_job_spec(
        self, request: SubmitRequest, input_uris: list[str]
    ) -> Any:
        """Build a google.cloud.batch_v1.Job protobuf.

        The job runs a single container task that:
          1. Pulls the pipeline Docker image
          2. Mounts GCS input/output paths via GCS FUSE
          3. Runs the Nextflow variant-calling pipeline
          4. Writes VCF + index to the output mount
        """
        from google.cloud import batch_v1

        # -- Container --
        container = batch_v1.Runnable.Container(
            image_uri=self._container_image,
            # entrypoint is baked into the Docker image (runs nextflow)
            commands=self._build_pipeline_command(request),
        )

        # Environment passed to the container
        env_vars = {
            "ANALYSIS_ID": str(request.analysis_id),
            "INPUT_TYPE": request.input_type,
            "REFERENCE_GENOME": request.reference_genome,
            "HLA_ALLELES": ",".join(request.hla_alleles),
            "TUMOR_NORMAL": "true" if request.tumor_normal_paired else "false",
            "INPUT_URIS": ",".join(input_uris),
            "OUTPUT_DIR": f"/mnt/output",
            "WORK_DIR": f"/mnt/work",
        }

        runnable = batch_v1.Runnable(
            container=container,
            environment=batch_v1.Environment(variables=env_vars),
        )

        # -- Task spec --
        task_spec = batch_v1.TaskSpec(
            runnables=[runnable],
            max_retry_count=1,
            max_run_duration="43200s",  # 12 hours max
        )

        # GCS volumes: inputs (read-only), outputs (read-write), work (read-write)
        gcs_prefix = f"jobs/{request.analysis_id}"
        task_spec.volumes = [
            batch_v1.Volume(
                gcs=batch_v1.GCS(remote_path=f"{self._bucket_name}/{gcs_prefix}/inputs"),
                mount_path="/mnt/inputs",
            ),
            batch_v1.Volume(
                gcs=batch_v1.GCS(remote_path=f"{self._bucket_name}/{gcs_prefix}/outputs"),
                mount_path="/mnt/output",
            ),
            batch_v1.Volume(
                gcs=batch_v1.GCS(remote_path=f"{self._bucket_name}/{gcs_prefix}/work"),
                mount_path="/mnt/work",
            ),
        ]

        # -- Resources --
        resources = batch_v1.ComputeResource(
            cpu_milli=request.cpu * 1000,
            memory_mib=request.memory_gb * 1024,
            boot_disk_mib=self._boot_disk_gb * 1024,
        )

        task_spec.compute_resource = resources

        # -- Task group (just 1 task) --
        task_group = batch_v1.TaskGroup(
            task_count=1,
            task_spec=task_spec,
            parallelism=1,
        )

        # -- Allocation policy --
        instance_policy = batch_v1.AllocationPolicy.InstancePolicy(
            machine_type=self._machine_type,
            # Provisioning model: use spot if extra.get("spot") is set
            provisioning_model=(
                batch_v1.AllocationPolicy.ProvisioningModel.SPOT
                if request.extra.get("spot", False)
                else batch_v1.AllocationPolicy.ProvisioningModel.STANDARD
            ),
        )

        instances = batch_v1.AllocationPolicy.InstancePolicyOrTemplate(
            policy=instance_policy,
        )

        allocation_policy = batch_v1.AllocationPolicy(
            instances=[instances],
        )

        # Attach service account so the container can read/write GCS
        if self._service_account:
            allocation_policy.service_account = (
                batch_v1.ServiceAccount(email=self._service_account)
            )

        # -- Labels for tracking --
        labels = {
            "app": "cvdash",
            "analysis-id": str(request.analysis_id),
            "input-type": request.input_type,
        }

        # -- Assemble job --
        job = batch_v1.Job(
            task_groups=[task_group],
            allocation_policy=allocation_policy,
            labels=labels,
            logs_policy=batch_v1.LogsPolicy(
                destination=batch_v1.LogsPolicy.Destination.CLOUD_LOGGING,
            ),
        )

        return job

    def _build_pipeline_command(self, request: SubmitRequest) -> list[str]:
        """Build the command list for the container entrypoint.

        The container image has Nextflow + all bioinformatics tools baked in.
        We invoke Nextflow with the appropriate pipeline profile.
        """
        cmd = [
            "nextflow", "run", "/pipeline/main.nf",
            "-profile", self._nextflow_profile,
            "--input_dir", "/mnt/inputs",
            "--output_dir", "/mnt/output",
            "--work_dir", "/mnt/work",
            "--reference", request.reference_genome,
            "--hla_alleles", ",".join(request.hla_alleles),
            "--input_type", request.input_type,
        ]

        if request.tumor_normal_paired:
            cmd.append("--paired")

        # Pass any extra Nextflow params from request.extra
        for k, v in request.extra.items():
            if k.startswith("nf_"):
                cmd.extend([f"--{k[3:]}", str(v)])

        return cmd

    # -- Public API (all async) ---

    async def submit(self, request: SubmitRequest) -> str:
        """Upload inputs to GCS, then submit a Batch job."""
        gcs_prefix = f"jobs/{request.analysis_id}/inputs"

        try:
            # Upload input files to GCS (potentially large, run in thread)
            input_uris = []
            for fpath in request.input_files:
                uri = await asyncio.to_thread(self._upload_file, fpath, gcs_prefix)
                input_uris.append(uri)

            logger.info(
                f"Uploaded {len(input_uris)} files for analysis {request.analysis_id}"
            )

            # Build and submit the Batch job
            job_spec = self._build_job_spec(request, input_uris)
            job_name = f"cvdash-{request.analysis_id}-{int(datetime.now(timezone.utc).timestamp())}"

            from google.cloud import batch_v1

            create_request = batch_v1.CreateJobRequest(
                parent=f"projects/{self._project}/locations/{self._region}",
                job=job_spec,
                job_id=job_name,
            )

            job = await asyncio.to_thread(
                self._get_batch_client().create_job, create_request
            )

            logger.info(
                f"Submitted GCP Batch job {job.name} for analysis {request.analysis_id}"
            )
            return job.name

        except Exception as e:
            raise SubmitError(
                f"Failed to submit GCP Batch job: {e}",
                backend=self.name,
                job_id="",
            ) from e

    async def poll(self, job_id: str) -> JobStatus:
        """Query Batch API for job status."""
        try:
            from google.cloud import batch_v1

            get_request = batch_v1.GetJobRequest(name=job_id)
            job = await asyncio.to_thread(
                self._get_batch_client().get_job, get_request
            )

            # Map GCP state to our enum
            gcp_state = batch_v1.JobStatus.State(job.status.state).name
            state = _GCP_STATE_MAP.get(gcp_state, JobState.PENDING)

            # Extract progress from status events if available
            progress = 0.0
            message = ""
            if job.status.status_events:
                latest = job.status.status_events[-1]
                message = latest.description or ""
                # Approximate progress from state
                if state == JobState.QUEUED:
                    progress = 0.05
                elif state == JobState.RUNNING:
                    progress = 0.3  # rough midpoint; Nextflow substeps not visible here
                elif state == JobState.SUCCEEDED:
                    progress = 1.0

            return JobStatus(
                job_id=job_id,
                state=state,
                progress_pct=progress,
                message=message,
                backend_meta={
                    "gcp_state": gcp_state,
                    "create_time": str(job.create_time) if job.create_time else "",
                    "update_time": str(job.update_time) if job.update_time else "",
                },
            )

        except Exception as e:
            raise PollError(
                f"Failed to poll GCP Batch job: {e}",
                backend=self.name,
                job_id=job_id,
            ) from e

    async def cancel(self, job_id: str) -> bool:
        """Delete the Batch job (GCP Batch has no pause/cancel, only delete)."""
        try:
            from google.cloud import batch_v1

            delete_request = batch_v1.DeleteJobRequest(
                name=job_id,
                reason="Cancelled by user via CVDash",
            )
            await asyncio.to_thread(
                self._get_batch_client().delete_job, delete_request
            )
            logger.info(f"Cancelled GCP Batch job {job_id}")
            return True

        except Exception as e:
            logger.warning(f"Failed to cancel GCP Batch job {job_id}: {e}")
            return False

    async def retrieve_results(self, job_id: str, dest_dir: str) -> JobResult:
        """Download VCF + index from GCS outputs prefix."""
        # Parse analysis_id from job name (format: cvdash-{analysis_id}-{timestamp})
        # Job name format: projects/{proj}/locations/{region}/jobs/cvdash-{id}-{ts}
        try:
            job_short = job_id.rsplit("/", 1)[-1]  # cvdash-{id}-{ts}
            parts = job_short.split("-")
            analysis_id = parts[1]
        except (IndexError, ValueError):
            raise RetrieveError(
                f"Cannot parse analysis ID from job name: {job_id}",
                backend=self.name,
                job_id=job_id,
            )

        output_prefix = f"jobs/{analysis_id}/outputs"

        try:
            blobs = await asyncio.to_thread(self._list_blobs, output_prefix)

            if not blobs:
                raise RetrieveError(
                    f"No output files found at gs://{self._bucket_name}/{output_prefix}",
                    backend=self.name,
                    job_id=job_id,
                )

            vcf_path = None
            extra_files = {}
            metrics = {}

            for blob_name in blobs:
                fname = blob_name.rsplit("/", 1)[-1]
                local_dest = os.path.join(dest_dir, fname)

                await asyncio.to_thread(self._download_blob, blob_name, local_dest)

                if fname.endswith(".vcf") or fname.endswith(".vcf.gz"):
                    vcf_path = local_dest
                elif fname.endswith(".json"):
                    # Likely QC metrics
                    try:
                        with open(local_dest) as f:
                            metrics[fname] = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        pass
                    extra_files[fname] = local_dest
                else:
                    extra_files[fname] = local_dest

            if not vcf_path:
                raise RetrieveError(
                    f"No VCF file in job outputs. Files found: {[b.rsplit('/', 1)[-1] for b in blobs]}",
                    backend=self.name,
                    job_id=job_id,
                )

            return JobResult(
                job_id=job_id,
                vcf_local_path=vcf_path,
                extra_files=extra_files,
                metrics=metrics,
            )

        except RetrieveError:
            raise
        except Exception as e:
            raise RetrieveError(
                f"Failed to retrieve results: {e}",
                backend=self.name,
                job_id=job_id,
            ) from e

    async def cleanup(self, job_id: str) -> None:
        """Delete all GCS objects for this job. Best-effort."""
        try:
            job_short = job_id.rsplit("/", 1)[-1]
            parts = job_short.split("-")
            analysis_id = parts[1]
            prefix = f"jobs/{analysis_id}"

            count = await asyncio.to_thread(self._delete_prefix, prefix)
            logger.info(f"Cleaned up {count} GCS objects for job {job_id}")

        except Exception as e:
            logger.warning(f"Cleanup failed for job {job_id}: {e}")
