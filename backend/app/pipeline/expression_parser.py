"""
Gene expression matrix parser.

Reads CSV/TSV files containing gene-level expression values (TPM, FPKM, or raw counts)
and returns a dict mapping gene_name -> TPM for use in the immunogenicity scorer.

Supported input formats:
  - Generic CSV/TSV with header row containing gene identifier + expression column
  - RSEM output (*.genes.results): gene_id, transcript_id(s), length, effective_length, expected_count, TPM, FPKM
  - Salmon quant.sf: Name, Length, EffectiveLength, TPM, NumReads
  - kallisto abundance.tsv: target_id, length, eff_length, est_counts, tpm
  - StringTie gene output: gene_id, gene_name, reference, strand, start, end, coverage, FPKM, TPM
  - HTSeq-count / featureCounts: gene_id + count columns (need external normalization)

For raw counts (no TPM column detected), we do a simple CPM normalization:
  CPM = (count / total_counts) * 1e6
This isn't as good as TPM but is a reasonable fallback when the user doesn't have
proper quantification output. We log a warning so they know.

Assumptions:
  - File has a header row
  - First column is gene ID/name (Ensembl, HGNC symbol, or mixed)
  - We try to find a TPM column first, then FPKM, then fall back to raw counts
  - Gene names containing dots (ENSG00000141510.16) get the version stripped
  - Duplicate gene names: keep the row with higher expression
"""
import csv
import io
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Column name patterns we look for (case-insensitive)
# Priority order: TPM > FPKM > counts
TPM_PATTERNS = re.compile(r"^(tpm|transcripts_per_million)$", re.IGNORECASE)
FPKM_PATTERNS = re.compile(r"^(fpkm|rpkm|fragments_per_kilobase_million)$", re.IGNORECASE)
COUNT_PATTERNS = re.compile(
    r"^(expected_count|est_counts|numreads|count|counts|raw_count|"
    r"uniq_reads|read_count)$",
    re.IGNORECASE,
)
GENE_ID_PATTERNS = re.compile(
    r"^(gene_id|gene_name|gene|name|target_id|geneid|symbol|ensembl_gene_id|hugo_symbol)$",
    re.IGNORECASE,
)

# Metadata columns that are NOT expression values (skip these in multi-sample detection)
METADATA_PATTERNS = re.compile(
    r"^(gene_id|gene_name|gene|name|target_id|geneid|symbol|ensembl_gene_id|hugo_symbol|"
    r"entrez_gene_id|entrez_geneid|transcript_id|length|effective_length|eff_length|"
    r"reference|strand|start|end|coverage|chr|chrom|description|biotype|status)$",
    re.IGNORECASE,
)

# Ensembl version suffix: ENSG00000141510.16 -> ENSG00000141510
ENSEMBL_VERSION_RE = re.compile(r"^(ENS[A-Z]*G\d+)\.\d+$")

# Min rows to consider the file valid (header + at least some genes)
MIN_GENE_ROWS = 5
# Max rows we'll read (safety valve for very large matrices)
MAX_GENE_ROWS = 200_000
# Max file size: 500 MB (expression matrices shouldn't be huge)
MAX_FILE_SIZE = 500 * 1024 * 1024


class ExpressionParseError(Exception):
    """Raised when we can't parse the expression matrix."""
    pass


def _strip_ensembl_version(gene_id: str) -> str:
    """Remove .version suffix from Ensembl gene IDs."""
    m = ENSEMBL_VERSION_RE.match(gene_id)
    return m.group(1) if m else gene_id


def _detect_delimiter(first_line: str) -> str:
    """Guess CSV vs TSV from the header line."""
    tabs = first_line.count("\t")
    commas = first_line.count(",")
    return "\t" if tabs >= commas else ","


def _find_column(headers: list[str], pattern: re.Pattern) -> Optional[int]:
    """Find the first column index matching a regex pattern."""
    for i, h in enumerate(headers):
        if pattern.match(h.strip()):
            return i
    return None


def _detect_expression_column(headers: list[str]) -> tuple[int, str]:
    """
    Find the best expression value column.
    Returns (column_index, unit_type) where unit_type is 'tpm', 'fpkm', 'counts',
    or 'multi_sample' (for batch matrices where sample IDs are columns).
    Raises ExpressionParseError if no suitable column found.
    """
    # Try TPM first (best for our purposes)
    idx = _find_column(headers, TPM_PATTERNS)
    if idx is not None:
        return idx, "tpm"

    # Then FPKM
    idx = _find_column(headers, FPKM_PATTERNS)
    if idx is not None:
        return idx, "fpkm"

    # Then raw counts
    idx = _find_column(headers, COUNT_PATTERNS)
    if idx is not None:
        return idx, "counts"

    # Last resort: if there are only 2 columns, assume col 0 = gene, col 1 = expression
    if len(headers) == 2:
        logger.warning(
            "No recognized expression column name. Assuming 2-column format "
            f"(gene, expression). Headers: {headers}"
        )
        return 1, "unknown"

    # Multi-sample matrix detection: if headers after the metadata columns
    # don't match any known metadata pattern, they're probably sample IDs
    # (e.g. TCGA barcodes, sample names). This is common for GDC/Firebrowse
    # RSEM batch downloads where columns are Hugo_Symbol, Entrez_Gene_Id,
    # then TCGA-XX-XXXX-01, TCGA-YY-YYYY-01, etc.
    sample_cols = []
    for i, h in enumerate(headers):
        if not METADATA_PATTERNS.match(h.strip()):
            sample_cols.append(i)

    if len(sample_cols) >= 1:
        logger.info(
            f"Detected multi-sample expression matrix with {len(sample_cols)} sample columns. "
            f"Will average across all samples. First sample: '{headers[sample_cols[0]]}'"
        )
        # Return the index of the first sample column; the parser will handle
        # averaging across all sample columns via the 'multi_sample' unit_type.
        return sample_cols[0], "multi_sample"

    raise ExpressionParseError(
        f"Could not find an expression value column in headers: {headers}. "
        "Expected one of: TPM, FPKM, expected_count, est_counts, NumReads, count, "
        "or sample ID columns for a multi-sample matrix. "
        "Please ensure your file has a header row with a recognized column name."
    )


def _detect_sample_columns(headers: list[str]) -> list[int]:
    """Return indices of all non-metadata columns (i.e. sample data columns)."""
    sample_cols = []
    for i, h in enumerate(headers):
        if not METADATA_PATTERNS.match(h.strip()):
            sample_cols.append(i)
    return sample_cols


def _fpkm_to_tpm(fpkm_values: dict[str, float]) -> dict[str, float]:
    """
    Convert FPKM to TPM.
    TPM_i = (FPKM_i / sum(FPKM)) * 1e6
    """
    total = sum(fpkm_values.values())
    if total <= 0:
        return fpkm_values
    return {gene: (fpkm / total) * 1e6 for gene, fpkm in fpkm_values.items()}


def _counts_to_cpm(count_values: dict[str, float]) -> dict[str, float]:
    """
    Convert raw counts to CPM (counts per million).
    Not as good as TPM but usable as a rough proxy.
    """
    total = sum(count_values.values())
    if total <= 0:
        return count_values
    return {gene: (count / total) * 1e6 for gene, count in count_values.items()}


def parse_expression_matrix(
    file_path: str | Path,
    gene_column: Optional[int] = None,
    value_column: Optional[int] = None,
) -> dict[str, float]:
    """
    Parse a gene expression matrix file and return gene -> TPM mapping.

    Args:
        file_path: Path to CSV/TSV expression file
        gene_column: Override for gene ID column index (0-based). Auto-detected if None.
        value_column: Override for expression value column index (0-based). Auto-detected if None.

    Returns:
        Dict mapping gene name/ID (str) to TPM value (float).
        Genes with zero or negative expression are excluded.

    Raises:
        ExpressionParseError: If file can't be parsed or has invalid structure.
        FileNotFoundError: If file doesn't exist.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Expression matrix not found: {file_path}")

    if file_path.stat().st_size > MAX_FILE_SIZE:
        raise ExpressionParseError(
            f"Expression file too large ({file_path.stat().st_size / 1e6:.0f} MB). "
            f"Maximum {MAX_FILE_SIZE / 1e6:.0f} MB."
        )

    # Read the file
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise ExpressionParseError(f"Failed to read expression file: {e}")

    lines = content.strip().split("\n")
    if len(lines) < 2:
        raise ExpressionParseError(
            "Expression file has fewer than 2 lines (need header + data)."
        )

    # Skip comment lines (some tools prepend # comments)
    data_start = 0
    for i, line in enumerate(lines):
        if not line.startswith("#"):
            data_start = i
            break

    header_line = lines[data_start]
    delimiter = _detect_delimiter(header_line)

    # Parse header
    reader = csv.reader(io.StringIO(header_line), delimiter=delimiter)
    headers = next(reader)
    headers = [h.strip().strip('"').strip("'") for h in headers]

    if len(headers) < 2:
        raise ExpressionParseError(
            f"Expression file needs at least 2 columns. Found {len(headers)}: {headers}"
        )

    # Detect columns
    if gene_column is None:
        gene_col = _find_column(headers, GENE_ID_PATTERNS)
        if gene_col is None:
            # Default: first column is gene
            gene_col = 0
            logger.info(f"No recognized gene ID column. Using first column: '{headers[0]}'")
    else:
        gene_col = gene_column

    if value_column is not None:
        val_col = value_column
        unit_type = "unknown"
    else:
        val_col, unit_type = _detect_expression_column(headers)

    # For multi-sample matrices, identify all sample columns for averaging
    sample_cols: list[int] = []
    if unit_type == "multi_sample":
        sample_cols = _detect_sample_columns(headers)
        logger.info(
            f"Expression matrix (multi-sample): gene_col={gene_col} ({headers[gene_col]}), "
            f"{len(sample_cols)} sample columns, "
            f"rows={len(lines) - data_start - 1}"
        )
    else:
        logger.info(
            f"Expression matrix: gene_col={gene_col} ({headers[gene_col]}), "
            f"value_col={val_col} ({headers[val_col]}), unit={unit_type}, "
            f"rows={len(lines) - data_start - 1}"
        )

    # Parse data rows
    raw_values: dict[str, float] = {}
    parse_errors = 0
    row_count = 0

    for line in lines[data_start + 1:]:
        if not line.strip():
            continue
        if row_count >= MAX_GENE_ROWS:
            logger.warning(f"Stopped reading at {MAX_GENE_ROWS} rows (safety limit)")
            break

        row_reader = csv.reader(io.StringIO(line), delimiter=delimiter)
        try:
            row = next(row_reader)
        except StopIteration:
            continue

        gene_raw = row[gene_col].strip().strip('"').strip("'") if gene_col < len(row) else ""
        if not gene_raw or gene_raw == "":
            continue

        # Clean gene ID
        gene = _strip_ensembl_version(gene_raw)

        # Extract expression value: single column or average across sample columns
        if unit_type == "multi_sample" and sample_cols:
            # Average across all sample columns for this gene
            vals = []
            for sc in sample_cols:
                if sc < len(row):
                    try:
                        v = float(row[sc].strip())
                        if v >= 0:
                            vals.append(v)
                    except (ValueError, IndexError):
                        pass
            if not vals:
                parse_errors += 1
                continue
            value = sum(vals) / len(vals)
        else:
            if len(row) <= val_col:
                parse_errors += 1
                continue
            try:
                value = float(row[val_col].strip())
            except (ValueError, IndexError):
                parse_errors += 1
                continue
            if value < 0:
                parse_errors += 1
                continue

        # Deduplicate: keep higher expression
        if gene in raw_values:
            if value > raw_values[gene]:
                raw_values[gene] = value
        else:
            raw_values[gene] = value

        row_count += 1

    if row_count < MIN_GENE_ROWS:
        raise ExpressionParseError(
            f"Only {row_count} valid gene rows found (minimum {MIN_GENE_ROWS}). "
            "Check that the file format is correct."
        )

    if parse_errors > row_count * 0.1:
        logger.warning(
            f"High parse error rate: {parse_errors}/{row_count + parse_errors} rows "
            "had issues. Check file format."
        )

    # Convert to TPM if needed
    if unit_type == "fpkm":
        logger.info("Converting FPKM to TPM")
        expression = _fpkm_to_tpm(raw_values)
    elif unit_type == "counts":
        logger.warning(
            "Raw counts detected. Converting to CPM as a rough proxy for TPM. "
            "For best results, provide TPM values from a proper quantification tool "
            "(Salmon, RSEM, kallisto)."
        )
        expression = _counts_to_cpm(raw_values)
    elif unit_type == "multi_sample":
        # Multi-sample matrix: values are already averaged across samples.
        # Assume they are TPM (standard for RSEM/GDC batch downloads).
        logger.info(
            f"Multi-sample matrix: averaged {len(sample_cols)} samples per gene. "
            "Treating values as TPM."
        )
        expression = raw_values
    else:
        # TPM or unknown 2-column format -- use as-is
        expression = raw_values

    # Filter out zero-expression genes (they contribute nothing to scoring)
    expression = {g: v for g, v in expression.items() if v > 0}

    if not expression:
        raise ExpressionParseError(
            "No genes with positive expression values found. "
            "Check that the expression column contains valid numeric TPM/FPKM/count values."
        )

    logger.info(
        f"Parsed expression matrix: {len(expression)} genes with expression > 0 "
        f"(from {row_count} total rows). "
        f"TPM range: {min(expression.values()):.2f} - {max(expression.values()):.2f}"
    )

    return expression


def validate_expression_file(file_path: str | Path) -> dict:
    """
    Validate an expression matrix file without fully parsing it.
    Returns a summary dict with file stats, or raises ExpressionParseError.

    Useful for quick validation at upload time before committing the file.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    size = file_path.stat().st_size
    if size > MAX_FILE_SIZE:
        raise ExpressionParseError(f"File too large ({size / 1e6:.0f} MB)")
    if size < 50:
        raise ExpressionParseError("File too small to contain expression data")

    # Read just enough to validate structure
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            # Skip comments
            header_line = ""
            for line in f:
                if not line.startswith("#"):
                    header_line = line
                    break

            if not header_line:
                raise ExpressionParseError("File appears empty (no data rows)")

            delimiter = _detect_delimiter(header_line)
            reader = csv.reader(io.StringIO(header_line), delimiter=delimiter)
            headers = [h.strip().strip('"') for h in next(reader)]

            if len(headers) < 2:
                raise ExpressionParseError(f"Need at least 2 columns, found {len(headers)}")

            # Detect expression column (this will raise if not found)
            val_col, unit_type = _detect_expression_column(headers)

            # For multi-sample, use the first sample column for validation
            check_col = val_col
            sample_count = 0
            if unit_type == "multi_sample":
                s_cols = _detect_sample_columns(headers)
                if s_cols:
                    check_col = s_cols[0]
                    sample_count = len(s_cols)

            # Count a few data rows to make sure they're parseable
            valid_rows = 0
            for i, line in enumerate(f):
                if i > 20:
                    break
                if not line.strip():
                    continue
                row_reader = csv.reader(io.StringIO(line), delimiter=delimiter)
                row = next(row_reader, None)
                if row and len(row) > check_col:
                    try:
                        float(row[check_col].strip())
                        valid_rows += 1
                    except ValueError:
                        pass

            if valid_rows < 3:
                raise ExpressionParseError(
                    "Could not parse numeric expression values from the first rows. "
                    "Check file format."
                )

    except ExpressionParseError:
        raise
    except Exception as e:
        raise ExpressionParseError(f"Error reading file: {e}")

    expr_col_name = (
        f"{sample_count} sample columns (multi-sample matrix)"
        if unit_type == "multi_sample"
        else headers[val_col]
    )

    return {
        "columns": headers[:5] + (["..."] if len(headers) > 5 else []),
        "expression_column": expr_col_name,
        "unit_type": unit_type,
        "delimiter": "tab" if delimiter == "\t" else "comma",
        "sample_valid_rows": valid_rows,
        "file_size_bytes": size,
    }
