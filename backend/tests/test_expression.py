"""
Tests for gene expression matrix parser.

Covers:
  - TSV with TPM column (standard format)
  - Salmon quant.sf format
  - CSV with raw counts (CPM fallback)
  - RSEM-style with FPKM (FPKM->TPM conversion)
  - Two-column fallback
  - Ensembl version stripping
  - Validation endpoint
  - Error cases: bad files, missing columns, too few rows
  - Expression data wired into scorer

Run: pytest tests/test_expression.py -v
"""
import os
import sys
import math
import pytest
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURE_DIR = Path(__file__).parent / "fixtures"


class TestExpressionParser:
    """Test expression matrix parsing across formats."""

    def test_parse_tpm_tsv(self):
        """Standard TSV with gene_id, gene_name, TPM columns."""
        from app.pipeline.expression_parser import parse_expression_matrix

        result = parse_expression_matrix(FIXTURE_DIR / "test_expression_tpm.tsv")

        # Should have 9 genes (PTEN has TPM=0.3 which is > 0, but check)
        assert len(result) >= 9
        # BRAF should be ~45.2 (TPM used directly)
        assert "ENSG00000157764" in result  # version stripped
        assert abs(result["ENSG00000157764"] - 45.2) < 0.1
        # EGFR should be highest
        assert result["ENSG00000146648"] > 100

    def test_parse_salmon_format(self):
        """Salmon quant.sf with Name, Length, EffectiveLength, TPM, NumReads."""
        from app.pipeline.expression_parser import parse_expression_matrix

        result = parse_expression_matrix(FIXTURE_DIR / "test_expression_salmon.tsv")

        assert "BRAF" in result
        assert abs(result["BRAF"] - 45.2) < 0.1
        assert "TP53" in result
        assert len(result) >= 6  # PTEN has 0.3 which is > 0

    def test_parse_counts_csv(self):
        """CSV with raw counts -- should convert to CPM."""
        from app.pipeline.expression_parser import parse_expression_matrix

        result = parse_expression_matrix(FIXTURE_DIR / "test_expression_counts.csv")

        assert len(result) == 10
        # Values should be CPM, not raw counts
        # Total counts = 1234+3456+567+12+2345+890+234+123+4567+8901 = 22329
        # EGFR CPM = (8901/22329)*1e6 = ~398665
        assert result["EGFR"] > 100000  # definitely CPM, not raw count of 8901

    def test_parse_fpkm_to_tpm(self):
        """RSEM-style output with both TPM and FPKM -- should pick TPM."""
        from app.pipeline.expression_parser import parse_expression_matrix

        result = parse_expression_matrix(FIXTURE_DIR / "test_expression_fpkm.tsv")

        # Should prefer TPM column over FPKM
        assert "ENSG00000157764" in result
        assert abs(result["ENSG00000157764"] - 45.2) < 0.1

    def test_parse_twocol_fallback(self):
        """Two-column CSV without recognized column names."""
        from app.pipeline.expression_parser import parse_expression_matrix

        # The 2-col fixture only has 3 rows which is below MIN_GENE_ROWS,
        # so we create a temp file with enough rows for the fallback test.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write("gene,value\n")
            f.write("BRAF,45.2\n")
            f.write("TP53,128.7\n")
            f.write("KRAS,12.1\n")
            f.write("NRAS,67.8\n")
            f.write("EGFR,156.3\n")
            f.write("BRCA1,5.6\n")
            f.name_for_test = f.name

        try:
            result = parse_expression_matrix(f.name_for_test)
            assert "BRAF" in result
            assert abs(result["BRAF"] - 45.2) < 0.1
        finally:
            os.unlink(f.name_for_test)

    def test_ensembl_version_stripping(self):
        """Ensembl IDs like ENSG00000141510.16 should strip the version."""
        from app.pipeline.expression_parser import _strip_ensembl_version

        assert _strip_ensembl_version("ENSG00000141510.16") == "ENSG00000141510"
        assert _strip_ensembl_version("ENSG00000157764.13") == "ENSG00000157764"
        # Non-Ensembl IDs should pass through unchanged
        assert _strip_ensembl_version("BRAF") == "BRAF"
        assert _strip_ensembl_version("TP53") == "TP53"

    def test_duplicate_genes_keeps_higher(self):
        """When a gene appears multiple times, keep the higher expression."""
        from app.pipeline.expression_parser import parse_expression_matrix

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tsv", delete=False
        ) as f:
            f.write("gene_id\tTPM\n")
            f.write("BRAF\t10.0\n")
            f.write("BRAF\t50.0\n")
            f.write("TP53\t20.0\n")
            f.write("KRAS\t30.0\n")
            f.write("NRAS\t40.0\n")
            f.write("EGFR\t60.0\n")
            f.name_for_test = f.name

        try:
            result = parse_expression_matrix(f.name_for_test)
            assert abs(result["BRAF"] - 50.0) < 0.1  # should keep the higher value
        finally:
            os.unlink(f.name_for_test)

    def test_zero_expression_excluded(self):
        """Genes with TPM=0 should be excluded."""
        from app.pipeline.expression_parser import parse_expression_matrix

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tsv", delete=False
        ) as f:
            f.write("gene_id\tTPM\n")
            f.write("BRAF\t0.0\n")
            f.write("TP53\t128.7\n")
            f.write("KRAS\t12.1\n")
            f.write("NRAS\t67.8\n")
            f.write("EGFR\t156.3\n")
            f.write("BRCA1\t5.6\n")
            f.name_for_test = f.name

        try:
            result = parse_expression_matrix(f.name_for_test)
            assert "BRAF" not in result  # zero expression excluded
            assert "TP53" in result
        finally:
            os.unlink(f.name_for_test)


class TestExpressionValidation:
    """Test the quick validation function."""

    def test_validate_tpm_tsv(self):
        """Validate a well-formed TPM file."""
        from app.pipeline.expression_parser import validate_expression_file

        info = validate_expression_file(FIXTURE_DIR / "test_expression_tpm.tsv")

        assert info["unit_type"] == "tpm"
        assert info["expression_column"] == "TPM"
        assert info["sample_valid_rows"] >= 3

    def test_validate_counts_csv(self):
        """Validate a counts file."""
        from app.pipeline.expression_parser import validate_expression_file

        info = validate_expression_file(FIXTURE_DIR / "test_expression_counts.csv")

        assert info["unit_type"] == "counts"
        assert info["delimiter"] == "comma"


class TestExpressionErrors:
    """Test error handling in expression parser."""

    def test_missing_file(self):
        from app.pipeline.expression_parser import parse_expression_matrix
        with pytest.raises(FileNotFoundError):
            parse_expression_matrix("/tmp/nonexistent_expression.tsv")

    def test_empty_file(self):
        from app.pipeline.expression_parser import parse_expression_matrix, ExpressionParseError

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tsv", delete=False
        ) as f:
            f.write("")
            f.name_for_test = f.name

        try:
            with pytest.raises(ExpressionParseError):
                parse_expression_matrix(f.name_for_test)
        finally:
            os.unlink(f.name_for_test)

    def test_no_expression_column(self):
        """File with only non-expression columns should fail."""
        from app.pipeline.expression_parser import parse_expression_matrix, ExpressionParseError

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tsv", delete=False
        ) as f:
            f.write("gene_id\tchrom\tstart\tend\n")
            f.write("BRAF\tchr7\t140719327\t140924929\n")
            f.write("TP53\tchr17\t7668402\t7687550\n")
            f.write("KRAS\tchr12\t25204789\t25250936\n")
            f.write("NRAS\tchr1\t114704464\t114716894\n")
            f.write("EGFR\tchr7\t55019017\t55211628\n")
            f.write("BRCA1\tchr17\t43044295\t43125483\n")
            f.name_for_test = f.name

        try:
            with pytest.raises(ExpressionParseError, match="Could not find"):
                parse_expression_matrix(f.name_for_test)
        finally:
            os.unlink(f.name_for_test)

    def test_too_few_rows(self):
        """File with < 5 data rows should fail."""
        from app.pipeline.expression_parser import parse_expression_matrix, ExpressionParseError

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tsv", delete=False
        ) as f:
            f.write("gene_id\tTPM\n")
            f.write("BRAF\t45.2\n")
            f.write("TP53\t128.7\n")
            f.name_for_test = f.name

        try:
            with pytest.raises(ExpressionParseError, match="Only 2 valid"):
                parse_expression_matrix(f.name_for_test)
        finally:
            os.unlink(f.name_for_test)

    def test_binary_file_rejected_by_validator(self):
        """Binary file should fail validation."""
        from app.pipeline.expression_parser import validate_expression_file, ExpressionParseError

        with tempfile.NamedTemporaryFile(suffix=".tsv", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
            f.name_for_test = f.name

        try:
            with pytest.raises(ExpressionParseError):
                validate_expression_file(f.name_for_test)
        finally:
            os.unlink(f.name_for_test)


class TestScorerWithExpression:
    """Test that expression data flows through to the scorer correctly."""

    def test_scorer_uses_expression_data(self):
        """When expression data is provided, scorer should use real TPM not default 0.5."""
        from app.pipeline.scorer import score_epitopes, _normalize_expression
        from app.pipeline.vcf_parser import ParsedVariant
        from app.pipeline.peptide_gen import CandidatePeptide
        from app.pipeline.mhc_predict import MHCPrediction

        variant = ParsedVariant(
            chrom="7", pos=140453136, ref="A", alt="T",
            gene="BRAF", protein_change="p.V600E",
            variant_type="missense", vaf=0.45,
            annotation={},
        )

        peptide = CandidatePeptide(
            peptide_seq="LATEKSRWS",
            peptide_length=9,
            variant=variant,
            mutation_position=4,
        )

        prediction = MHCPrediction(
            peptide_seq="LATEKSRWS",
            hla_allele="HLA-A*02:01",
            binding_affinity_nm=50.0,
            presentation_score=0.85,
            processing_score=0.7,
        )

        # Without expression data -- should use default 0.5
        scored_no_expr = score_epitopes([peptide], [prediction], expression_data=None)
        assert len(scored_no_expr) == 1
        assert scored_no_expr[0].expression_component == 0.5  # default

        # With expression data for BRAF at high TPM
        expr = {"BRAF": 100.0}
        scored_with_expr = score_epitopes([peptide], [prediction], expression_data=expr)
        assert len(scored_with_expr) == 1
        # High TPM should give higher expression component than the default 0.5
        assert scored_with_expr[0].expression_component > 0.5
        assert scored_with_expr[0].expression_tpm == 100.0

        # With expression data showing BRAF not expressed
        expr_low = {"BRAF": 0.1}
        scored_low = score_epitopes([peptide], [prediction], expression_data=expr_low)
        assert scored_low[0].expression_component == 0.0  # TPM < 1 -> 0

        # The overall score should be higher with high expression
        assert scored_with_expr[0].immunogenicity_score > scored_low[0].immunogenicity_score

    def test_normalize_expression_function(self):
        """Test the TPM normalization directly."""
        from app.pipeline.scorer import _normalize_expression

        # None -> default 0.5
        assert _normalize_expression(None) == 0.5

        # TPM < 1 -> 0.0 (not expressed)
        assert _normalize_expression(0.0) == 0.0
        assert _normalize_expression(0.5) == 0.0

        # TPM = 1 -> small positive value
        assert 0 < _normalize_expression(1.0) < 0.3

        # TPM = 10 -> moderate
        norm_10 = _normalize_expression(10.0)
        assert 0.3 < norm_10 < 0.7

        # TPM = 100 -> approaching 1.0
        norm_100 = _normalize_expression(100.0)
        assert norm_100 >= 0.95

        # TPM = 1000 -> capped at 1.0
        assert _normalize_expression(1000.0) == 1.0

        # Monotonically increasing
        assert _normalize_expression(1.0) < _normalize_expression(10.0)
        assert _normalize_expression(10.0) < _normalize_expression(50.0)
        assert _normalize_expression(50.0) < _normalize_expression(100.0)
