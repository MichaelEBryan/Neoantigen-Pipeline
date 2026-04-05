"""
MAF (Mutation Annotation Format) parser.

Parses TCGA-style MAF files (.maf, .txt) into ParsedVariant objects
compatible with the rest of the pipeline. These files come from tools
like Genome Nexus, cBioPortal, Funcotator, oncotator, maf2maf, etc.

Expected columns (standard MAF spec):
  Hugo_Symbol, Chromosome, Start_Position, End_Position,
  Reference_Allele, Tumor_Seq_Allele2, Variant_Classification,
  Variant_Type, HGVSp_Short, Transcript_ID, t_ref_count, t_alt_count

Column lookup is case-insensitive and tolerates minor naming variations
(e.g. Tumor_Seq_Allele2 vs tumor_seq_allele2).
"""
import csv
import logging
from pathlib import Path
from typing import Optional

from .vcf_parser import ParsedVariant, ParseResult

logger = logging.getLogger(__name__)

# MAF Variant_Classification values that correspond to coding mutations.
# Keys are lowercased for case-insensitive matching. Values are our internal types.
MAF_CLASSIFICATION_MAP = {
    "missense_mutation": "missense",
    "missense": "missense",
    "nonsense_mutation": "nonsense",
    "stop_gained": "nonsense",
    "nonstop_mutation": "missense",
    "frame_shift_del": "frameshift",
    "frame_shift_ins": "frameshift",
    "frameshift_variant": "frameshift",
    "in_frame_del": "inframe_indel",
    "in_frame_ins": "inframe_indel",
    "inframe_insertion": "inframe_indel",
    "inframe_deletion": "inframe_indel",
}

# Classifications we skip entirely (non-coding, silent, etc.)
SKIP_CLASSIFICATIONS = {
    "silent",
    "synonymous_variant",
    "intron",
    "intron_variant",
    "3'utr",
    "3'flank",
    "5'utr",
    "5'flank",
    "igr",
    "rna",
    "splice_site",
    "splice_region",
    "translation_start_site",
    "de_novo_start_outofframe",
    "de_novo_start_inframe",
}

# Standard MAF column names (lowercased for matching)
# We map common synonyms to canonical names.
COLUMN_ALIASES = {
    "hugo_symbol": "gene",
    "gene": "gene",
    "gene_symbol": "gene",
    "chromosome": "chrom",
    "chrom": "chrom",
    "chr": "chrom",
    "start_position": "pos",
    "start_pos": "pos",
    "pos": "pos",
    "position": "pos",
    "end_position": "end_pos",
    "reference_allele": "ref",
    "ref": "ref",
    "tumor_seq_allele2": "alt",
    "alt": "alt",
    "alternate_allele": "alt",
    "tumor_seq_allele1": "ref_allele1",
    "variant_classification": "classification",
    "variant_type": "variant_type",
    "hgvsp_short": "protein_change",
    "hgvsp": "protein_change_long",
    "protein_change": "protein_change",
    "amino_acid_change": "protein_change",
    "transcript_id": "transcript",
    "t_ref_count": "t_ref_count",
    "t_alt_count": "t_alt_count",
    "t_depth": "t_depth",
    "n_ref_count": "n_ref_count",
    "n_alt_count": "n_alt_count",
    "tumor_sample_barcode": "sample",
    "ncbi_build": "build",
}


def _normalize_chrom(chrom: str) -> str:
    """Ensure chromosome has 'chr' prefix."""
    if not chrom.startswith("chr"):
        return f"chr{chrom}"
    return chrom


def _compute_vaf(row: dict) -> Optional[float]:
    """Compute VAF from t_ref_count and t_alt_count if available."""
    try:
        t_alt = int(row.get("t_alt_count", "0") or "0")
        t_ref = int(row.get("t_ref_count", "0") or "0")
        total = t_alt + t_ref
        if total > 0:
            return round(t_alt / total, 4)
    except (ValueError, TypeError):
        pass

    # Try t_depth
    try:
        t_alt = int(row.get("t_alt_count", "0") or "0")
        t_depth = int(row.get("t_depth", "0") or "0")
        if t_depth > 0:
            return round(t_alt / t_depth, 4)
    except (ValueError, TypeError):
        pass

    return None


def _map_columns(header: list[str]) -> dict[str, int]:
    """
    Map header column names to canonical field names.
    Returns dict of canonical_name -> column_index.
    """
    mapped = {}
    for i, col in enumerate(header):
        key = col.strip().lower()
        canonical = COLUMN_ALIASES.get(key)
        if canonical and canonical not in mapped:
            mapped[canonical] = i
    return mapped


def is_maf_header(line: str) -> bool:
    """
    Check if a line looks like a MAF header row.
    Looks for at least 3 of the key MAF columns.
    """
    lower = line.lower()
    key_columns = ["hugo_symbol", "variant_classification", "chromosome",
                   "start_position", "reference_allele", "tumor_seq_allele2"]
    matches = sum(1 for col in key_columns if col in lower)
    return matches >= 3


def parse_maf(
    maf_path: str | Path,
    min_vaf: float = 0.0,
) -> ParseResult:
    """
    Parse a MAF file and extract coding somatic variants.

    Args:
        maf_path: Path to MAF or .txt file in MAF format
        min_vaf: Minimum VAF to include (0.0 = no filter)

    Returns:
        ParseResult with variants list and metadata about skipped records.
    """
    maf_path = Path(maf_path)
    variants = []
    total = 0
    skipped_noncoding = 0
    skipped_vaf = 0
    skipped_parse = 0
    consequence_counts: dict[str, int] = {}

    with open(maf_path, "r", encoding="utf-8", errors="replace") as f:
        # Skip comment lines (start with # or are blank)
        header_line = None
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            header_line = stripped
            break

        if not header_line:
            logger.warning(f"MAF file {maf_path} appears empty (no header found)")
            return ParseResult(
                variants=[],
                total_records=0,
                skipped_filter=0,
                skipped_noncoding=0,
                skipped_vaf=0,
                consequence_counts={},
            )

        # Parse header
        header_cols = header_line.split("\t")
        col_map = _map_columns(header_cols)

        # Verify we have minimum required columns
        required = {"gene", "chrom", "pos", "ref", "alt", "classification"}
        missing = required - set(col_map.keys())
        if missing:
            # Try with just gene + classification + protein_change (some MAFs lack genomic coords)
            if "gene" not in col_map or "classification" not in col_map:
                raise ValueError(
                    f"MAF file missing required columns: {missing}. "
                    f"Found columns: {[c.strip() for c in header_cols[:20]]}"
                )
            logger.warning(
                f"MAF file missing some columns ({missing}), "
                "will use available fields"
            )

        # Parse data rows
        reader = csv.reader(f, delimiter="\t")
        for row_fields in reader:
            if not row_fields or row_fields[0].startswith("#"):
                continue
            total += 1

            # Build a dict of canonical_name -> value for this row
            row = {}
            for canonical, idx in col_map.items():
                if idx < len(row_fields):
                    row[canonical] = row_fields[idx].strip()
                else:
                    row[canonical] = ""

            # Check variant classification
            classification = row.get("classification", "").lower().strip()
            if not classification:
                skipped_noncoding += 1
                continue

            if classification in SKIP_CLASSIFICATIONS:
                skipped_noncoding += 1
                continue

            variant_type = MAF_CLASSIFICATION_MAP.get(classification)
            if variant_type is None:
                # Unknown classification -- skip with warning for first few
                if skipped_noncoding < 5:
                    logger.debug(f"Skipping unknown classification: {classification}")
                skipped_noncoding += 1
                continue

            # Extract genomic coordinates
            chrom = row.get("chrom", "")
            pos_str = row.get("pos", "")
            ref = row.get("ref", "")
            alt = row.get("alt", "")

            if not chrom or not pos_str:
                # Some MAFs might not have coords -- skip these
                skipped_parse += 1
                continue

            try:
                pos = int(pos_str)
            except ValueError:
                skipped_parse += 1
                continue

            # Handle cases where alt == ref (Tumor_Seq_Allele2 == Reference_Allele)
            # This sometimes happens; the actual alt might be in Tumor_Seq_Allele1
            if alt == ref:
                alt2 = row.get("ref_allele1", "")
                if alt2 and alt2 != ref:
                    alt = alt2
                else:
                    skipped_parse += 1
                    continue

            # VAF
            vaf = _compute_vaf(row)
            if vaf is not None and vaf < min_vaf:
                skipped_vaf += 1
                continue

            # Protein change
            protein_change = row.get("protein_change", "")
            if not protein_change:
                # Try long form (HGVSp) and extract short form
                long_form = row.get("protein_change_long", "")
                if long_form and ":" in long_form:
                    protein_change = long_form.split(":")[-1]
                elif long_form:
                    protein_change = long_form

            # Ensure protein_change starts with p.
            if protein_change and not protein_change.startswith("p."):
                protein_change = f"p.{protein_change}"

            gene = row.get("gene", "")

            parsed = ParsedVariant(
                chrom=_normalize_chrom(chrom),
                pos=pos,
                ref=ref if ref and ref != "-" else "",
                alt=alt if alt and alt != "-" else "",
                gene=gene or None,
                protein_change=protein_change or None,
                variant_type=variant_type,
                vaf=vaf,
                consequence=classification,
                annotation={
                    "source": "maf",
                    "classification": row.get("classification", ""),
                    "transcript": row.get("transcript", ""),
                    "sample": row.get("sample", ""),
                },
            )
            variants.append(parsed)
            consequence_counts[classification] = consequence_counts.get(classification, 0) + 1

    logger.info(
        f"MAF parsed: {total} rows, {len(variants)} coding variants kept. "
        f"Skipped: {skipped_noncoding} non-coding, {skipped_vaf} low-VAF, "
        f"{skipped_parse} unparseable"
    )
    return ParseResult(
        variants=variants,
        total_records=total,
        skipped_filter=0,  # MAF doesn't have a FILTER field
        skipped_noncoding=skipped_noncoding,
        skipped_vaf=skipped_vaf,
        consequence_counts=consequence_counts,
    )
