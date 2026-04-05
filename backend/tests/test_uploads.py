"""
Tests for file upload and analysis submission endpoints.

Tests the upload validation (extensions, magic bytes), the submit endpoint
(HLA validation, file requirement), and the full flow.
"""
import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFileValidation:
    """Test extension and magic bytes validation helpers."""

    def test_get_extension_vcf(self):
        from app.routers.uploads import _get_extension
        assert _get_extension("sample.vcf") == ".vcf"
        assert _get_extension("sample.vcf.gz") == ".vcf.gz"
        assert _get_extension("SAMPLE.VCF") == ".vcf"

    def test_get_extension_bam(self):
        from app.routers.uploads import _get_extension
        assert _get_extension("aligned.bam") == ".bam"

    def test_get_extension_fastq(self):
        from app.routers.uploads import _get_extension
        assert _get_extension("reads.fastq") == ".fastq"
        assert _get_extension("reads.fastq.gz") == ".fastq.gz"
        assert _get_extension("reads.fq") == ".fq"
        assert _get_extension("reads.fq.gz") == ".fq.gz"

    def test_get_extension_unsupported(self):
        from app.routers.uploads import _get_extension
        assert _get_extension("script.py") is None
        assert _get_extension("noextension") is None

    def test_get_extension_expression_formats(self):
        from app.routers.uploads import _get_extension
        assert _get_extension("data.csv") == ".csv"
        assert _get_extension("quant.tsv") == ".tsv"
        assert _get_extension("counts.txt") == ".txt"


class TestFilenameSanitization:
    """Test path traversal prevention."""

    def test_strips_directory_traversal(self):
        from app.routers.uploads import _sanitize_filename
        result = _sanitize_filename("../../../etc/passwd")
        assert "/" not in result
        assert ".." not in result
        assert "passwd" in result

    def test_strips_windows_path(self):
        from app.routers.uploads import _sanitize_filename
        result = _sanitize_filename("C:\\Users\\evil\\..\\..\\file.vcf")
        assert "\\" not in result
        assert "file.vcf" in result

    def test_strips_null_bytes(self):
        from app.routers.uploads import _sanitize_filename
        result = _sanitize_filename("test\x00.vcf")
        assert "\x00" not in result

    def test_empty_becomes_upload(self):
        from app.routers.uploads import _sanitize_filename
        result = _sanitize_filename("")
        assert "upload" in result

    def test_normal_filename_preserved(self):
        from app.routers.uploads import _sanitize_filename
        result = _sanitize_filename("sample_tumor.vcf")
        assert "sample_tumor.vcf" in result

    def test_has_uuid_prefix(self):
        from app.routers.uploads import _sanitize_filename
        result = _sanitize_filename("test.vcf")
        # Should have format: 8hex_test.vcf
        parts = result.split("_", 1)
        assert len(parts) == 2
        assert len(parts[0]) == 8

    def test_validate_magic_bytes_vcf(self):
        from app.routers.uploads import _validate_magic_bytes
        header = b"##fileformat=VCFv4.2\n##INFO=..."
        assert _validate_magic_bytes(header, ".vcf") is True

    def test_validate_magic_bytes_vcf_bad(self):
        from app.routers.uploads import _validate_magic_bytes
        header = b"this is not a vcf file"
        assert _validate_magic_bytes(header, ".vcf") is False

    def test_validate_magic_bytes_bam(self):
        from app.routers.uploads import _validate_magic_bytes
        header = b"BAM\x01" + b"\x00" * 60
        assert _validate_magic_bytes(header, ".bam") is True

    def test_validate_magic_bytes_bam_bad(self):
        from app.routers.uploads import _validate_magic_bytes
        header = b"not a bam file"
        assert _validate_magic_bytes(header, ".bam") is False

    def test_validate_magic_bytes_fastq(self):
        from app.routers.uploads import _validate_magic_bytes
        header = b"@SEQ_ID\nGATTTGGGGT\n+\n!''*((((***"
        assert _validate_magic_bytes(header, ".fastq") is True

    def test_validate_magic_bytes_gzip(self):
        from app.routers.uploads import _validate_magic_bytes
        # gzip magic bytes
        header = b"\x1f\x8b" + b"\x00" * 62
        assert _validate_magic_bytes(header, ".vcf.gz") is True
        assert _validate_magic_bytes(header, ".fastq.gz") is True

    def test_validate_magic_bytes_gzip_bad(self):
        from app.routers.uploads import _validate_magic_bytes
        header = b"\x00\x00" + b"\x00" * 62
        assert _validate_magic_bytes(header, ".vcf.gz") is False


class TestHLAValidation:
    """Test HLA allele validation in the submit endpoint."""

    def test_normalize_hla(self):
        from app.routers.analyses import _normalize_hla
        assert _normalize_hla("A*02:01") == "HLA-A*02:01"
        assert _normalize_hla("HLA-B*44:02") == "HLA-B*44:02"
        assert _normalize_hla("  c*07:01  ") == "HLA-C*07:01"

    def test_hla_pattern_valid(self):
        from app.routers.analyses import HLA_PATTERN
        valid = [
            "HLA-A*02:01", "HLA-B*44:02", "HLA-C*07:01",
            "HLA-A*24:02", "HLA-B*15:01",
        ]
        for allele in valid:
            assert HLA_PATTERN.match(allele), f"Should match: {allele}"

    def test_hla_pattern_invalid(self):
        from app.routers.analyses import HLA_PATTERN
        invalid = [
            "HLA-D*02:01",    # not Class I
            "A02:01",          # missing asterisk
            "HLA-A*02",        # missing second field
            "random string",
            "",
        ]
        for allele in invalid:
            assert not HLA_PATTERN.match(allele), f"Should NOT match: {allele}"

    def test_submit_request_validation_too_many_alleles(self):
        from app.routers.analyses import SubmitAnalysisRequest
        with pytest.raises(Exception):  # Pydantic ValidationError
            SubmitAnalysisRequest(
                hla_alleles=[f"HLA-A*0{i}:01" for i in range(7)]
            )

    def test_submit_request_validation_bad_format(self):
        from app.routers.analyses import SubmitAnalysisRequest
        with pytest.raises(Exception):
            SubmitAnalysisRequest(hla_alleles=["not-an-allele"])

    def test_submit_request_normalizes(self):
        from app.routers.analyses import SubmitAnalysisRequest
        req = SubmitAnalysisRequest(hla_alleles=["A*02:01", "B*44:02"])
        assert req.hla_alleles == ["HLA-A*02:01", "HLA-B*44:02"]

    def test_submit_request_none_alleles(self):
        from app.routers.analyses import SubmitAnalysisRequest
        req = SubmitAnalysisRequest(hla_alleles=None)
        assert req.hla_alleles is None

    def test_submit_request_empty_becomes_none(self):
        from app.routers.analyses import SubmitAnalysisRequest
        req = SubmitAnalysisRequest(hla_alleles=[])
        assert req.hla_alleles is None

    def test_submit_request_rejects_too_many_per_locus(self):
        """Humans are diploid -- max 2 alleles per locus."""
        from app.routers.analyses import SubmitAnalysisRequest
        with pytest.raises(Exception):
            SubmitAnalysisRequest(
                hla_alleles=["HLA-A*01:01", "HLA-A*02:01", "HLA-A*03:01"]
            )

    def test_submit_request_allows_two_per_locus(self):
        from app.routers.analyses import SubmitAnalysisRequest
        req = SubmitAnalysisRequest(
            hla_alleles=["HLA-A*01:01", "HLA-A*02:01", "HLA-B*44:02", "HLA-B*08:01"]
        )
        assert len(req.hla_alleles) == 4


class TestAnalysisCreate:
    """Test analysis creation validation."""

    def test_valid_input_types(self):
        from app.routers.analyses import AnalysisCreate
        for t in ["vcf", "bam", "fastq"]:
            req = AnalysisCreate(project_id=1, input_type=t)
            assert req.input_type == t

    def test_invalid_input_type(self):
        from app.routers.analyses import AnalysisCreate
        with pytest.raises(Exception):
            AnalysisCreate(project_id=1, input_type="pdf")

    def test_invalid_input_type_empty(self):
        from app.routers.analyses import AnalysisCreate
        with pytest.raises(Exception):
            AnalysisCreate(project_id=1, input_type="")
