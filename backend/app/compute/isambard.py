"""
Isambard HPC backend for variant calling.

Connects to the Isambard Phase 3 (AIRR) login node via SSH/SFTP,
uploads input files, submits a Slurm batch job running the Nextflow
variant-calling pipeline inside Singularity containers, polls sacct
for status, and downloads the resulting VCF.

Isambard Phase 3 specifics:
    - Login node: login.isambard.ac.uk (Phase 3 AIRR)
    - Scheduler: Slurm
    - Containers: Singularity (pulled from a shared /projects path)
    - Filesystem: Lustre scratch at /scratch/projects/<project>
    - GPU nodes: NVIDIA GH200 (not needed for variant calling)
    - Typical BWA-MEM2 + Mutect2 job: 16 cores, 64GB, ~45 min for WES

Requires:
    pip install paramiko

    SSH key must be set up:
        - Key path in config: isambard_key_path
        - Public key registered with Isambard admin
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

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
# Slurm state to our JobState
# ---------------------------------------------------------------------------
_SLURM_STATE_MAP: dict[str, JobState] = {
    "PENDING": JobState.QUEUED,
    "CONFIGURING": JobState.QUEUED,
    "RUNNING": JobState.RUNNING,
    "COMPLETING": JobState.RUNNING,
    "COMPLETED": JobState.SUCCEEDED,
    "FAILED": JobState.FAILED,
    "TIMEOUT": JobState.FAILED,
    "OUT_OF_MEMORY": JobState.FAILED,
    "NODE_FAIL": JobState.FAILED,
    "CANCELLED": JobState.CANCELLED,
    "PREEMPTED": JobState.CANCELLED,
}


def _settings():
    from app.config import settings
    return settings


class IsambardBackend:
    """ComputeBackend implementation using SSH + Slurm on Isambard.

    All SSH/SFTP operations are blocking (paramiko), so every public
    method wraps them in asyncio.to_thread.
    """

    def __init__(self):
        s = _settings()
        self._host = s.isambard_host
        self._user = s.isambard_user
        self._key_path = s.isambard_key_path
        self._project_dir = getattr(s, "isambard_project_dir", "/scratch/projects/cvdash")
        self._container_dir = getattr(s, "isambard_container_dir", "/projects/cvdash/containers")
        self._nextflow_path = getattr(s, "isambard_nextflow_path", "/projects/cvdash/nextflow")
        self._partition = getattr(s, "isambard_partition", "cpu")
        self._account = getattr(s, "isambard_account", "")

        # Paramiko client is NOT thread-safe. We create a fresh one per
        # operation rather than holding a persistent connection. This is
        # safer for a web server with concurrent requests and long gaps
        # between calls.
        self._ssh = None

    @property
    def name(self) -> str:
        return "isambard"

    # -- SSH helpers (all sync, called via to_thread) ---

    def _connect(self):
        """Create a fresh SSH connection. Caller must close it."""
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        key_path = os.path.expanduser(self._key_path)
        if not os.path.exists(key_path):
            raise SubmitError(
                f"SSH key not found: {key_path}. "
                "Configure isambard_key_path in settings.",
                backend=self.name,
            )

        client.connect(
            hostname=self._host,
            username=self._user,
            key_filename=key_path,
            timeout=30,
        )
        return client

    def _exec(self, client, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        """Run a command over SSH. Returns (stdout, stderr, exit_code)."""
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        return stdout.read().decode().strip(), stderr.read().decode().strip(), exit_code

    def _sftp_upload(self, client, local_path: str, remote_path: str) -> None:
        """Upload a file via SFTP."""
        sftp = client.open_sftp()
        try:
            # Ensure parent directory exists
            remote_dir = str(PurePosixPath(remote_path).parent)
            self._exec(client, f"mkdir -p {remote_dir}")

            logger.info(f"SFTP upload: {local_path} -> {remote_path}")
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()

    def _sftp_download(self, client, remote_path: str, local_path: str) -> None:
        """Download a file via SFTP."""
        sftp = client.open_sftp()
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            logger.info(f"SFTP download: {remote_path} -> {local_path}")
            sftp.get(remote_path, local_path)
        finally:
            sftp.close()

    def _sftp_list(self, client, remote_dir: str) -> list[str]:
        """List files in a remote directory."""
        sftp = client.open_sftp()
        try:
            return sftp.listdir(remote_dir)
        except FileNotFoundError:
            return []
        finally:
            sftp.close()

    # -- Slurm script builder ---

    def _build_sbatch_script(self, request: SubmitRequest, remote_input_dir: str) -> str:
        """Generate a Slurm batch script for the Nextflow pipeline.

        The script:
          1. Loads Singularity module
          2. Runs Nextflow with the variant-calling pipeline
          3. Writes outputs to the job's output directory
        """
        job_dir = f"{self._project_dir}/jobs/{request.analysis_id}"
        output_dir = f"{job_dir}/outputs"
        work_dir = f"{job_dir}/work"

        # Estimate walltime based on input type
        # BAM alignment is much faster than FASTQ (already aligned)
        walltime = "04:00:00" if request.input_type == "fastq" else "02:00:00"

        account_line = f"#SBATCH --account={self._account}" if self._account else ""

        script = textwrap.dedent(f"""\
            #!/bin/bash
            #SBATCH --job-name=cvdash-{request.analysis_id}
            #SBATCH --partition={self._partition}
            #SBATCH --nodes=1
            #SBATCH --ntasks=1
            #SBATCH --cpus-per-task={request.cpu}
            #SBATCH --mem={request.memory_gb}G
            #SBATCH --time={walltime}
            #SBATCH --output={job_dir}/slurm-%j.out
            #SBATCH --error={job_dir}/slurm-%j.err
            {account_line}

            # --- Environment setup ---
            module load singularity 2>/dev/null || true
            mkdir -p {output_dir} {work_dir}

            echo "CVDash analysis {request.analysis_id} starting at $(date)"
            echo "Input type: {request.input_type}"
            echo "Reference: {request.reference_genome}"
            echo "HLA alleles: {','.join(request.hla_alleles)}"

            # --- Run Nextflow pipeline ---
            {self._nextflow_path}/nextflow run \\
                {self._nextflow_path}/main.nf \\
                -profile singularity \\
                --input_dir {remote_input_dir} \\
                --output_dir {output_dir} \\
                --work_dir {work_dir} \\
                --reference {request.reference_genome} \\
                --hla_alleles "{','.join(request.hla_alleles)}" \\
                --input_type {request.input_type} \\
                {'--paired' if request.tumor_normal_paired else ''} \\
                --container_dir {self._container_dir} \\
                -with-report {output_dir}/nextflow_report.html \\
                -with-trace {output_dir}/nextflow_trace.tsv

            EXIT_CODE=$?
            echo "Pipeline finished with exit code $EXIT_CODE at $(date)"
            exit $EXIT_CODE
        """)

        return script

    # -- Sync implementations (run via to_thread) ---

    def _do_submit(self, request: SubmitRequest) -> str:
        """Upload files + submit sbatch. Returns Slurm job ID as string."""
        client = self._connect()
        try:
            job_dir = f"{self._project_dir}/jobs/{request.analysis_id}"
            input_dir = f"{job_dir}/inputs"

            # Create directories
            self._exec(client, f"mkdir -p {input_dir}")

            # Upload input files
            for fpath in request.input_files:
                fname = Path(fpath).name
                self._sftp_upload(client, fpath, f"{input_dir}/{fname}")

            logger.info(
                f"Uploaded {len(request.input_files)} files to {input_dir}"
            )

            # Write sbatch script
            script = self._build_sbatch_script(request, input_dir)
            script_path = f"{job_dir}/run.sh"

            # Upload script via SFTP (write to temp, then move)
            sftp = client.open_sftp()
            try:
                with sftp.open(script_path, "w") as f:
                    f.write(script)
            finally:
                sftp.close()

            self._exec(client, f"chmod +x {script_path}")

            # Submit
            stdout, stderr, code = self._exec(client, f"sbatch {script_path}")
            if code != 0:
                raise SubmitError(
                    f"sbatch failed (exit {code}): {stderr}",
                    backend=self.name,
                )

            # Parse "Submitted batch job 12345" -> "12345"
            match = re.search(r"Submitted batch job (\d+)", stdout)
            if not match:
                raise SubmitError(
                    f"Could not parse sbatch output: {stdout}",
                    backend=self.name,
                )

            slurm_id = match.group(1)
            logger.info(
                f"Submitted Slurm job {slurm_id} for analysis {request.analysis_id}"
            )
            return slurm_id

        finally:
            client.close()

    def _do_poll(self, job_id: str) -> JobStatus:
        """Query sacct for job status."""
        client = self._connect()
        try:
            # sacct with parseable output
            stdout, stderr, code = self._exec(
                client,
                f"sacct -j {job_id} --format=JobID,State,Elapsed,MaxRSS,ExitCode "
                f"--noheader --parsable2",
            )

            if code != 0 or not stdout:
                # Job might not be in sacct yet -- try squeue
                sq_out, _, sq_code = self._exec(
                    client,
                    f"squeue -j {job_id} --format=%T --noheader",
                )
                if sq_code == 0 and sq_out:
                    state_str = sq_out.strip().split("\n")[0]
                    state = _SLURM_STATE_MAP.get(state_str, JobState.PENDING)
                    return JobStatus(
                        job_id=job_id,
                        state=state,
                        progress_pct=0.05 if state == JobState.QUEUED else 0.3,
                        message=f"Slurm state: {state_str}",
                        backend_meta={"slurm_state": state_str},
                    )
                # Not in squeue either -- might be very new
                return JobStatus(
                    job_id=job_id,
                    state=JobState.PENDING,
                    message="Job not yet visible in scheduler",
                )

            # Parse the first line (the main job, not .batch or .extern steps)
            lines = [l for l in stdout.split("\n") if l and not ".batch" in l and not ".extern" in l]
            if not lines:
                return JobStatus(job_id=job_id, state=JobState.PENDING)

            fields = lines[0].split("|")
            state_str = fields[1] if len(fields) > 1 else "UNKNOWN"
            # Slurm states can have suffixes like "CANCELLED by 12345"
            base_state = state_str.split()[0] if state_str else "UNKNOWN"
            state = _SLURM_STATE_MAP.get(base_state, JobState.PENDING)

            elapsed = fields[2] if len(fields) > 2 else ""
            max_rss = fields[3] if len(fields) > 3 else ""
            exit_code = fields[4] if len(fields) > 4 else ""

            progress = {
                JobState.QUEUED: 0.05,
                JobState.RUNNING: 0.3,
                JobState.SUCCEEDED: 1.0,
                JobState.FAILED: 1.0,
                JobState.CANCELLED: 1.0,
            }.get(state, 0.0)

            return JobStatus(
                job_id=job_id,
                state=state,
                progress_pct=progress,
                message=f"Slurm: {base_state}, elapsed: {elapsed}",
                backend_meta={
                    "slurm_state": base_state,
                    "elapsed": elapsed,
                    "max_rss": max_rss,
                    "exit_code": exit_code,
                },
            )

        finally:
            client.close()

    def _do_cancel(self, job_id: str) -> bool:
        """Send scancel."""
        client = self._connect()
        try:
            _, stderr, code = self._exec(client, f"scancel {job_id}")
            if code != 0:
                logger.warning(f"scancel failed for {job_id}: {stderr}")
                return False
            return True
        finally:
            client.close()

    def _do_retrieve(self, job_id: str, analysis_id: str, dest_dir: str) -> JobResult:
        """Download outputs from Isambard scratch."""
        client = self._connect()
        try:
            output_dir = f"{self._project_dir}/jobs/{analysis_id}/outputs"
            files = self._sftp_list(client, output_dir)

            if not files:
                raise RetrieveError(
                    f"No output files at {output_dir}",
                    backend=self.name,
                    job_id=job_id,
                )

            vcf_path = None
            extra_files = {}

            for fname in files:
                remote = f"{output_dir}/{fname}"
                local = os.path.join(dest_dir, fname)

                self._sftp_download(client, remote, local)

                if fname.endswith(".vcf") or fname.endswith(".vcf.gz"):
                    vcf_path = local
                else:
                    extra_files[fname] = local

            if not vcf_path:
                raise RetrieveError(
                    f"No VCF in outputs. Files: {files}",
                    backend=self.name,
                    job_id=job_id,
                )

            return JobResult(
                job_id=job_id,
                vcf_local_path=vcf_path,
                extra_files=extra_files,
            )

        finally:
            client.close()

    def _do_cleanup(self, analysis_id: str) -> None:
        """Remove job directory from Isambard scratch."""
        client = self._connect()
        try:
            job_dir = f"{self._project_dir}/jobs/{analysis_id}"
            _, stderr, code = self._exec(
                client, f"rm -rf {job_dir}", timeout=120
            )
            if code != 0:
                logger.warning(f"Cleanup failed for {job_dir}: {stderr}")
        finally:
            client.close()

    # -- Public async API ---

    async def submit(self, request: SubmitRequest) -> str:
        try:
            return await asyncio.to_thread(self._do_submit, request)
        except SubmitError:
            raise
        except Exception as e:
            raise SubmitError(
                f"Isambard submit failed: {e}",
                backend=self.name,
            ) from e

    async def poll(self, job_id: str) -> JobStatus:
        try:
            return await asyncio.to_thread(self._do_poll, job_id)
        except PollError:
            raise
        except Exception as e:
            raise PollError(
                f"Isambard poll failed: {e}",
                backend=self.name,
                job_id=job_id,
            ) from e

    async def cancel(self, job_id: str) -> bool:
        try:
            return await asyncio.to_thread(self._do_cancel, job_id)
        except Exception as e:
            logger.warning(f"Isambard cancel failed for {job_id}: {e}")
            return False

    async def retrieve_results(self, job_id: str, dest_dir: str) -> JobResult:
        """Retrieve results. Needs analysis_id, which we store in the
        job metadata. For Isambard, the job_id IS the Slurm job ID,
        and we need the analysis_id separately. We encode it in the
        job metadata passed through the dispatch layer.

        Convention: the dispatch layer stores a mapping
        {slurm_job_id -> analysis_id} in Redis or the DB. The caller
        passes the analysis_id in the dest_dir path as a hint:
        dest_dir = /app/results/{analysis_id}/
        """
        # Extract analysis_id from dest_dir (convention: last path component)
        analysis_id = Path(dest_dir).name
        try:
            return await asyncio.to_thread(
                self._do_retrieve, job_id, analysis_id, dest_dir
            )
        except RetrieveError:
            raise
        except Exception as e:
            raise RetrieveError(
                f"Isambard retrieve failed: {e}",
                backend=self.name,
                job_id=job_id,
            ) from e

    async def cleanup(self, job_id: str) -> None:
        """Cleanup is keyed by analysis_id. Same convention as retrieve."""
        # Best-effort, so we just log failures
        try:
            # For cleanup we need the analysis_id. If we only have the slurm
            # job ID we can't do much. In practice the dispatch layer calls
            # this with the analysis_id.
            await asyncio.to_thread(self._do_cleanup, job_id)
        except Exception as e:
            logger.warning(f"Isambard cleanup failed: {e}")
