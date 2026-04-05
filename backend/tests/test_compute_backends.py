"""
Tests for the compute backend abstraction layer.

Tests both GCP Batch and Isambard backends using mocks (no real cloud/HPC
connections). Validates the protocol, factory, dispatch orchestrator, and
error handling.
"""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

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
    get_compute_backend,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_request():
    return SubmitRequest(
        analysis_id=42,
        input_files=["/data/tumor_R1.fastq.gz", "/data/tumor_R2.fastq.gz"],
        input_type="fastq",
        hla_alleles=["HLA-A*02:01", "HLA-B*44:02"],
        reference_genome="GRCh38",
        tumor_normal_paired=False,
        cpu=16,
        memory_gb=32,
    )


@pytest.fixture
def sample_request_bam():
    return SubmitRequest(
        analysis_id=43,
        input_files=["/data/tumor.bam"],
        input_type="bam",
        hla_alleles=["HLA-A*02:01"],
        reference_genome="GRCh37",
    )


# ---------------------------------------------------------------------------
# Protocol and data type tests
# ---------------------------------------------------------------------------

class TestJobState:
    def test_terminal_states(self):
        assert JobState.SUCCEEDED.is_terminal
        assert JobState.FAILED.is_terminal
        assert JobState.CANCELLED.is_terminal

    def test_non_terminal_states(self):
        assert not JobState.PENDING.is_terminal
        assert not JobState.QUEUED.is_terminal
        assert not JobState.RUNNING.is_terminal

    def test_string_values(self):
        assert JobState.RUNNING == "running"
        assert JobState.SUCCEEDED == "succeeded"


class TestSubmitRequest:
    def test_defaults(self, sample_request):
        assert sample_request.cpu == 16
        assert sample_request.memory_gb == 32
        assert sample_request.gpu == 0
        assert sample_request.extra == {}

    def test_bam_request(self, sample_request_bam):
        assert sample_request_bam.input_type == "bam"
        assert sample_request_bam.reference_genome == "GRCh37"
        assert sample_request_bam.cpu == 16  # default


class TestJobStatus:
    def test_defaults(self):
        s = JobStatus(job_id="123", state=JobState.RUNNING)
        assert s.progress_pct == 0.0
        assert s.message == ""
        assert s.backend_meta == {}


class TestExceptions:
    def test_compute_error_fields(self):
        e = ComputeError("something broke", backend="gcp-batch", job_id="j-123")
        assert e.backend == "gcp-batch"
        assert e.job_id == "j-123"
        assert "something broke" in str(e)

    def test_submit_error_inherits(self):
        assert issubclass(SubmitError, ComputeError)

    def test_poll_error_inherits(self):
        assert issubclass(PollError, ComputeError)

    def test_retrieve_error_inherits(self):
        assert issubclass(RetrieveError, ComputeError)


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

class TestFactory:
    @patch("app.compute.backend.settings", create=True)
    def test_get_gcp_backend(self, mock_settings):
        """Factory returns GCPBatchBackend when config says gcp-batch."""
        mock_settings.compute_backend = "gcp-batch"
        # Patch GCPBatchBackend init to avoid reading real config
        with patch("app.compute.gcp_batch.GCPBatchBackend.__init__", return_value=None):
            backend = get_compute_backend("gcp-batch")
            assert backend.name == "gcp-batch"

    @patch("app.compute.backend.settings", create=True)
    def test_get_isambard_backend(self, mock_settings):
        """Factory returns IsambardBackend when config says isambard."""
        mock_settings.compute_backend = "isambard"
        with patch("app.compute.isambard.IsambardBackend.__init__", return_value=None):
            backend = get_compute_backend("isambard")
            assert backend.name == "isambard"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown compute backend"):
            get_compute_backend("aws-batch")


# ---------------------------------------------------------------------------
# GCP Batch backend tests
# ---------------------------------------------------------------------------

class TestGCPBatchBackend:
    """Tests for GCPBatchBackend using mocked GCP clients."""

    @pytest.fixture
    def backend(self):
        """Create a GCPBatchBackend with mocked config."""
        with patch("app.compute.gcp_batch._settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                gcp_project_id="test-project",
                gcp_region="europe-west2",
                gcp_pipeline_bucket="test-bucket",
                gcp_service_account="sa@test.iam.gserviceaccount.com",
                gcp_pipeline_image="gcr.io/test/pipeline:latest",
                gcp_machine_type="n2-standard-16",
                gcp_boot_disk_gb=200,
                gcp_nextflow_profile="docker",
            )
            from app.compute.gcp_batch import GCPBatchBackend
            b = GCPBatchBackend()
        return b

    def test_name(self, backend):
        assert backend.name == "gcp-batch"

    def test_build_pipeline_command_fastq(self, backend, sample_request):
        cmd = backend._build_pipeline_command(sample_request)
        assert "nextflow" in cmd[0]
        assert "--input_type" in cmd
        assert "fastq" in cmd
        assert "--reference" in cmd
        assert "GRCh38" in cmd

    def test_build_pipeline_command_paired(self, backend, sample_request):
        sample_request.tumor_normal_paired = True
        cmd = backend._build_pipeline_command(sample_request)
        assert "--paired" in cmd

    def test_build_pipeline_command_extra_nf_params(self, backend, sample_request):
        sample_request.extra = {"nf_min_reads": "1000", "spot": True}
        cmd = backend._build_pipeline_command(sample_request)
        assert "--min_reads" in cmd
        assert "1000" in cmd

    @pytest.fixture
    def mock_batch_v1(self):
        """Create a mock google.cloud.batch_v1 module and inject it into sys.modules."""
        import sys
        mock_bv1 = MagicMock()
        mock_bv1.CreateJobRequest = MagicMock
        mock_bv1.DeleteJobRequest = MagicMock
        mock_bv1.GetJobRequest = MagicMock
        mock_bv1.Runnable = MagicMock()
        mock_bv1.Runnable.Container = MagicMock
        mock_bv1.Environment = MagicMock
        mock_bv1.TaskSpec = MagicMock(return_value=MagicMock(volumes=[]))
        mock_bv1.Volume = MagicMock
        mock_bv1.GCS = MagicMock
        mock_bv1.ComputeResource = MagicMock
        mock_bv1.TaskGroup = MagicMock
        mock_bv1.AllocationPolicy = MagicMock()
        mock_bv1.AllocationPolicy.InstancePolicy = MagicMock
        mock_bv1.AllocationPolicy.InstancePolicyOrTemplate = MagicMock
        mock_bv1.AllocationPolicy.ProvisioningModel = MagicMock()
        mock_bv1.AllocationPolicy.ProvisioningModel.STANDARD = 1
        mock_bv1.AllocationPolicy.ProvisioningModel.SPOT = 2
        mock_bv1.ServiceAccount = MagicMock
        mock_bv1.Job = MagicMock
        mock_bv1.LogsPolicy = MagicMock()
        mock_bv1.LogsPolicy.Destination = MagicMock()
        mock_bv1.LogsPolicy.Destination.CLOUD_LOGGING = 1
        mock_bv1.BatchServiceClient = MagicMock

        # Also mock the parent packages so `from google.cloud import batch_v1` works
        old_google = sys.modules.get("google")
        old_gc = sys.modules.get("google.cloud")
        old_bv1 = sys.modules.get("google.cloud.batch_v1")
        old_storage = sys.modules.get("google.cloud.storage")

        google_mod = MagicMock()
        gc_mod = MagicMock()
        gc_mod.batch_v1 = mock_bv1
        gc_mod.storage = MagicMock()
        google_mod.cloud = gc_mod

        sys.modules["google"] = google_mod
        sys.modules["google.cloud"] = gc_mod
        sys.modules["google.cloud.batch_v1"] = mock_bv1
        sys.modules["google.cloud.storage"] = gc_mod.storage

        yield mock_bv1

        # Restore
        if old_google is None:
            sys.modules.pop("google", None)
        else:
            sys.modules["google"] = old_google
        if old_gc is None:
            sys.modules.pop("google.cloud", None)
        else:
            sys.modules["google.cloud"] = old_gc
        if old_bv1 is None:
            sys.modules.pop("google.cloud.batch_v1", None)
        else:
            sys.modules["google.cloud.batch_v1"] = old_bv1
        if old_storage is None:
            sys.modules.pop("google.cloud.storage", None)
        else:
            sys.modules["google.cloud.storage"] = old_storage

    @pytest.mark.asyncio
    async def test_submit_uploads_and_creates_job(self, backend, sample_request, mock_batch_v1):
        """Submit should upload files then create a Batch job."""
        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        mock_storage = MagicMock()
        mock_storage.bucket.return_value = mock_bucket
        backend._storage_client = mock_storage

        # Mock the Batch client -- create_job returns a job-like object
        mock_job = MagicMock()
        mock_job.name = "projects/test/locations/eu/jobs/cvdash-42-1234"
        mock_batch = MagicMock()
        mock_batch.create_job.return_value = mock_job
        backend._batch_client = mock_batch

        # CreateJobRequest must accept kwargs including 'parent' (which is
        # reserved by MagicMock). Use a plain class instead.
        class FakeRequest:
            def __init__(self, **kw):
                self.__dict__.update(kw)
        mock_batch_v1.CreateJobRequest = FakeRequest

        job_id = await backend.submit(sample_request)

        assert "cvdash-42" in job_id
        # Should have uploaded 2 files
        assert mock_blob.upload_from_filename.call_count == 2

    @pytest.mark.asyncio
    async def test_submit_failure_raises_submit_error(self, backend, sample_request):
        mock_storage = MagicMock()
        mock_storage.bucket.side_effect = Exception("Auth failed")
        backend._storage_client = mock_storage

        with pytest.raises(SubmitError, match="Auth failed"):
            await backend.submit(sample_request)

    @pytest.mark.asyncio
    async def test_cancel_returns_true_on_success(self, backend, mock_batch_v1):
        mock_batch = MagicMock()
        mock_batch.delete_job.return_value = None
        backend._batch_client = mock_batch

        result = await backend.cancel("projects/p/locations/r/jobs/j-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_returns_false_on_failure(self, backend, mock_batch_v1):
        mock_batch = MagicMock()
        mock_batch.delete_job.side_effect = Exception("Not found")
        backend._batch_client = mock_batch

        result = await backend.cancel("projects/p/locations/r/jobs/j-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_retrieve_results_downloads_vcf(self, backend, tmp_path):
        """retrieve_results should download blobs and find the VCF."""
        # Patch _list_blobs to return plain strings (avoids MagicMock .name issues)
        blob_names = [
            "jobs/42/outputs/sample.annotated.vcf.gz",
            "jobs/42/outputs/qc_metrics.json",
        ]

        def fake_download(blob_path, local_dest):
            os.makedirs(os.path.dirname(local_dest), exist_ok=True)
            Path(local_dest).write_text("fake")
            return local_dest

        with patch.object(backend, "_list_blobs", return_value=blob_names), \
             patch.object(backend, "_download_blob", side_effect=fake_download):
            job_id = "projects/p/locations/r/jobs/cvdash-42-1234"
            result = await backend.retrieve_results(job_id, str(tmp_path))

        assert result.vcf_local_path.endswith(".vcf.gz")
        assert "qc_metrics.json" in result.extra_files

    @pytest.mark.asyncio
    async def test_retrieve_no_vcf_raises(self, backend, tmp_path):
        """Should raise RetrieveError if no VCF in outputs."""
        blob_names = ["jobs/42/outputs/only_a_log.txt"]

        def fake_download(blob_path, local_dest):
            os.makedirs(os.path.dirname(local_dest), exist_ok=True)
            Path(local_dest).write_text("x")
            return local_dest

        with patch.object(backend, "_list_blobs", return_value=blob_names), \
             patch.object(backend, "_download_blob", side_effect=fake_download):
            with pytest.raises(RetrieveError, match="No VCF"):
                await backend.retrieve_results(
                    "projects/p/locations/r/jobs/cvdash-42-1234",
                    str(tmp_path),
                )

    @pytest.mark.asyncio
    async def test_cleanup_deletes_prefix(self, backend):
        mock_bucket = MagicMock()
        mock_bucket.list_blobs.return_value = [MagicMock(), MagicMock()]
        mock_storage = MagicMock()
        mock_storage.bucket.return_value = mock_bucket
        backend._storage_client = mock_storage

        await backend.cleanup("projects/p/locations/r/jobs/cvdash-42-1234")
        mock_bucket.delete_blobs.assert_called_once()


# ---------------------------------------------------------------------------
# Isambard backend tests
# ---------------------------------------------------------------------------

class TestIsambardBackend:
    """Tests for IsambardBackend using mocked SSH/SFTP."""

    @pytest.fixture
    def backend(self):
        with patch("app.compute.isambard._settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                isambard_host="login.isambard.ac.uk",
                isambard_user="testuser",
                isambard_key_path="/tmp/test_key",
                isambard_project_dir="/scratch/projects/cvdash",
                isambard_container_dir="/projects/cvdash/containers",
                isambard_nextflow_path="/projects/cvdash/nextflow",
                isambard_partition="cpu",
                isambard_account="cvdash-proj",
            )
            from app.compute.isambard import IsambardBackend
            b = IsambardBackend()
        return b

    def test_name(self, backend):
        assert backend.name == "isambard"

    def test_build_sbatch_script(self, backend, sample_request):
        script = backend._build_sbatch_script(sample_request, "/scratch/inputs")
        assert "#!/bin/bash" in script
        assert "#SBATCH --job-name=cvdash-42" in script
        assert "#SBATCH --partition=cpu" in script
        assert "#SBATCH --cpus-per-task=16" in script
        assert "#SBATCH --mem=32G" in script
        assert "nextflow run" in script
        assert "--reference GRCh38" in script
        assert "HLA-A*02:01" in script
        assert "--account=cvdash-proj" in script

    def test_build_sbatch_script_bam_walltime(self, backend, sample_request_bam):
        """BAM jobs should get shorter walltime than FASTQ."""
        script = backend._build_sbatch_script(sample_request_bam, "/scratch/inputs")
        assert "--time=02:00:00" in script

    def test_build_sbatch_script_fastq_walltime(self, backend, sample_request):
        script = backend._build_sbatch_script(sample_request, "/scratch/inputs")
        assert "--time=04:00:00" in script

    @pytest.mark.asyncio
    async def test_submit_uploads_and_sbatch(self, backend, sample_request):
        """Submit should SFTP files and run sbatch."""
        mock_client = MagicMock()
        # exec_command returns (stdin, stdout, stderr) channels
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"Submitted batch job 12345"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp

        with patch.object(backend, "_connect", return_value=mock_client):
            with patch("os.path.exists", return_value=True):
                job_id = await backend.submit(sample_request)

        assert job_id == "12345"

    @pytest.mark.asyncio
    async def test_submit_sbatch_failure(self, backend, sample_request):
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 1
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"sbatch: error: Batch job submission failed"
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
        mock_sftp = MagicMock()
        mock_client.open_sftp.return_value = mock_sftp

        with patch.object(backend, "_connect", return_value=mock_client):
            with pytest.raises(SubmitError, match="sbatch failed"):
                await backend.submit(sample_request)

    @pytest.mark.asyncio
    async def test_poll_running(self, backend):
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"12345|RUNNING|01:23:45|4096K|0:0"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        with patch.object(backend, "_connect", return_value=mock_client):
            status = await backend.poll("12345")

        assert status.state == JobState.RUNNING
        assert "RUNNING" in status.message

    @pytest.mark.asyncio
    async def test_poll_completed(self, backend):
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"12345|COMPLETED|02:15:30|8192K|0:0"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        with patch.object(backend, "_connect", return_value=mock_client):
            status = await backend.poll("12345")

        assert status.state == JobState.SUCCEEDED
        assert status.progress_pct == 1.0

    @pytest.mark.asyncio
    async def test_poll_falls_back_to_squeue(self, backend):
        """If sacct returns nothing, should try squeue."""
        mock_client = MagicMock()
        # First call (sacct) returns empty
        stdout_sacct = MagicMock()
        stdout_sacct.read.return_value = b""
        stdout_sacct.channel.recv_exit_status.return_value = 0
        stderr_sacct = MagicMock()
        stderr_sacct.read.return_value = b""

        # Second call (squeue) returns PENDING
        stdout_squeue = MagicMock()
        stdout_squeue.read.return_value = b"PENDING"
        stdout_squeue.channel.recv_exit_status.return_value = 0
        stderr_squeue = MagicMock()
        stderr_squeue.read.return_value = b""

        mock_client.exec_command.side_effect = [
            (None, stdout_sacct, stderr_sacct),
            (None, stdout_squeue, stderr_squeue),
        ]

        with patch.object(backend, "_connect", return_value=mock_client):
            status = await backend.poll("12345")

        assert status.state == JobState.QUEUED

    @pytest.mark.asyncio
    async def test_cancel(self, backend):
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        with patch.object(backend, "_connect", return_value=mock_client):
            result = await backend.cancel("12345")

        assert result is True

    @pytest.mark.asyncio
    async def test_retrieve_downloads_vcf(self, backend, tmp_path):
        mock_client = MagicMock()
        mock_sftp = MagicMock()
        mock_sftp.listdir.return_value = [
            "sample.final.vcf.gz",
            "nextflow_report.html",
        ]
        # Make sftp.get create the file
        def fake_get(remote, local):
            Path(local).write_text("fake")

        mock_sftp.get = fake_get
        mock_client.open_sftp.return_value = mock_sftp

        # exec_command for mkdir
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        # dest_dir convention: last component = analysis_id
        dest = tmp_path / "42"
        dest.mkdir()

        with patch.object(backend, "_connect", return_value=mock_client):
            result = await backend.retrieve_results("12345", str(dest))

        assert result.vcf_local_path.endswith(".vcf.gz")
        assert "nextflow_report.html" in result.extra_files


# ---------------------------------------------------------------------------
# Dispatch orchestrator tests
# ---------------------------------------------------------------------------

class TestDispatch:
    """Tests for the dispatch_and_wait orchestrator."""

    @pytest.mark.asyncio
    async def test_build_submit_request(self):
        """build_submit_request should read files and HLA from DB."""
        from app.compute.dispatch import build_submit_request

        # Mock DB session
        mock_db = AsyncMock()
        mock_analysis = MagicMock()
        mock_analysis.id = 42
        mock_analysis.metadata_json = {"reference_genome": "GRCh37"}

        # Mock input files query
        mock_input = MagicMock()
        mock_input.file_path = "/uploads/tumor.bam"
        mock_input.file_type = "bam"
        inputs_result = MagicMock()
        inputs_result.scalars.return_value = MagicMock(all=lambda: [mock_input])

        # Mock HLA query
        mock_hla = MagicMock()
        mock_hla.allele = "HLA-A*02:01"
        hla_result = MagicMock()
        hla_result.scalars.return_value = MagicMock(all=lambda: [mock_hla])

        mock_db.execute = AsyncMock(side_effect=[inputs_result, hla_result])

        request = await build_submit_request(mock_db, mock_analysis)

        assert request.analysis_id == 42
        assert request.input_type == "bam"
        assert request.reference_genome == "GRCh37"
        assert "HLA-A*02:01" in request.hla_alleles

    @pytest.mark.asyncio
    async def test_build_submit_request_no_inputs_raises(self):
        from app.compute.dispatch import build_submit_request

        mock_db = AsyncMock()
        mock_analysis = MagicMock(id=42, metadata_json=None)

        empty_result = MagicMock()
        empty_result.scalars.return_value = MagicMock(all=lambda: [])
        mock_db.execute = AsyncMock(return_value=empty_result)

        with pytest.raises(ValueError, match="No input files"):
            await build_submit_request(mock_db, mock_analysis)

    @pytest.mark.asyncio
    async def test_build_submit_request_no_hla_raises(self):
        from app.compute.dispatch import build_submit_request

        mock_db = AsyncMock()
        mock_analysis = MagicMock(id=42, metadata_json=None)

        mock_input = MagicMock(file_path="/uploads/t.bam", file_type="bam")
        inputs_result = MagicMock()
        inputs_result.scalars.return_value = MagicMock(all=lambda: [mock_input])

        empty_hla = MagicMock()
        empty_hla.scalars.return_value = MagicMock(all=lambda: [])

        mock_db.execute = AsyncMock(side_effect=[inputs_result, empty_hla])

        with pytest.raises(ValueError, match="No HLA alleles"):
            await build_submit_request(mock_db, mock_analysis)
