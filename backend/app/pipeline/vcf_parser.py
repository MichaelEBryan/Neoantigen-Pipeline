"""
VCF parser using cyvcf2.

Extracts somatic coding variants (missense, frameshift, inframe indel)
from an annotated VCF file. Expects VEP/SnpEff/funcotator annotations
in the INFO field.

Supports multiple annotation tools:
- VEP/SnpEff: Standard CSQ/ANN fields
- DRAGEN (pyensembl/varcode): Substitution, FrameShift, PrematureStop, etc.
- Nirvana (Illumina): missense_variant, frameshift_variant, coding_sequence_variant
- Funcotator: Custom annotations

Assumptions:
- VCF is already filtered for somatic variants (PASS or no FILTER).
- Annotations are in INFO/CSQ (VEP) or INFO/ANN (SnpEff) or
  INFO/FUNCOTATION (Funcotator). We try all three.
- GRCh37/38 coordinates. Chromosome prefix (chr) handled either way.
- Dynamic CSQ header parsing for flexible field ordering.
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cyvcf2 import VCF

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Result of parsing a VCF/MAF file with metadata for error reporting."""
    variants: list
    total_records: int
    skipped_filter: int
    skipped_noncoding: int
    skipped_vaf: int
    consequence_counts: dict[str, int]  # all consequence terms seen -> count


# Variant types we care about for neoantigen prediction.
# All keys are lowercase for case-insensitive matching.
CODING_CONSEQUENCES = {
    # Standard VEP terms
    "missense_variant",
    "frameshift_variant",
    "inframe_insertion",
    "inframe_deletion",
    "stop_gained",
    "protein_altering_variant",
    "start_lost",
    "stop_lost",
    "coding_sequence_variant",
    "initiator_codon_variant",
    # SnpEff terms
    "missense",
    "frameshift",
    "inframe_ins",
    "inframe_del",
    "stop_gained",
    # pyensembl/varcode (DRAGEN) terms
    "substitution",
    "frameshift",
    "prematurestop",
    "alternatestarcodon",
    "inframeinsertion",
    "inframedeletion",
    "exonicsplicesite",
    "stoploss",
    "startloss",
    # Case variations and synonyms seen in the wild
    "missense_mutation",
    "nonsense_mutation",
    "frame_shift_del",
    "frame_shift_ins",
    "in_frame_del",
    "in_frame_ins",
    "nonstop_mutation",
    "splice_site",
}

# Simplified category for scoring (frameshift gets a bonus).
# Maps any coding consequence term to our internal type.
# All keys are lowercase.
VARIANT_TYPE_MAP = {
    # Standard VEP
    "missense_variant": "missense",
    "missense": "missense",
    "missense_mutation": "missense",
    "frameshift_variant": "frameshift",
    "frameshift": "frameshift",
    "frame_shift_del": "frameshift",
    "frame_shift_ins": "frameshift",
    "inframe_insertion": "inframe_indel",
    "inframe_deletion": "inframe_indel",
    "inframe_ins": "inframe_indel",
    "inframe_del": "inframe_indel",
    "in_frame_del": "inframe_indel",
    "in_frame_ins": "inframe_indel",
    "stop_gained": "nonsense",
    "protein_altering_variant": "missense",
    "start_lost": "missense",
    "stop_lost": "missense",
    "coding_sequence_variant": "missense",
    "initiator_codon_variant": "missense",
    "nonsense_mutation": "nonsense",
    "nonstop_mutation": "nonsense",
    # DRAGEN/pyensembl
    "substitution": "missense",
    "prematurestop": "nonsense",
    "alternatestarcodon": "missense",
    "inframeinsertion": "inframe_indel",
    "inframedeletion": "inframe_indel",
    "exonicsplicesite": "missense",
    "stoploss": "missense",
    "startloss": "missense",
    "splice_site": "missense",
}


@dataclass
class ParsedVariant:
    """A somatic coding variant extracted from VCF."""
    chrom: str
    pos: int
    ref: str
    alt: str
    gene: Optional[str] = None
    protein_change: Optional[str] = None  # e.g. p.V600E
    variant_type: str = "missense"        # missense/frameshift/inframe_indel/nonsense
    vaf: Optional[float] = None
    consequence: Optional[str] = None     # raw VEP/SnpEff term
    annotation: dict = field(default_factory=dict)  # full annotation blob


def _parse_csq_format_header(vcf) -> dict[str, int]:
    """
    Parse the CSQ format header from VCF to map field names to indices.

    Looks for: ##INFO=<ID=CSQ,...,Description="...Format: field1|field2|..."

    Returns dict mapping field name -> index, e.g. {"Allele": 0, "Consequence": 1, ...}
    """
    for header_line in vcf.header_iter():
        if header_line["ID"] == "CSQ":
            desc = header_line.get("Description", "")
            # Extract "Format: ..." portion
            match = re.search(r"Format:\s*([^\"]*)", desc)
            if match:
                format_str = match.group(1).strip()
                fields = [f.strip() for f in format_str.split("|")]
                field_map = {fname: idx for idx, fname in enumerate(fields)}
                logger.debug(f"Parsed CSQ header with {len(field_map)} fields: {list(field_map.keys())}")
                return field_map

    # Fallback to default VEP field order if header not found
    logger.debug("CSQ header not found, using default VEP field order")
    return {
        "Allele": 0,
        "Consequence": 1,
        "IMPACT": 2,
        "SYMBOL": 3,
        "Gene": 4,
        "Feature_type": 5,
        "Feature": 6,
        "BIOTYPE": 7,
        "EXON": 8,
        "INTRON": 9,
        "HGVSc": 10,
        "HGVSp": 11,
        "cDNA_position": 12,
        "CDS_position": 13,
        "Protein_position": 14,
        "Amino_acids": 15,
        "Codons": 16,
    }


def _parse_vep_csq(info_csq: str, field_map: dict[str, int]) -> list[dict]:
    """
    Parse VEP CSQ field using field position mapping from header.

    Args:
        info_csq: Comma-separated string of CSQ entries
        field_map: Dict mapping field name to index (from CSQ header)

    Returns:
        List of dicts with parsed annotation (consequence, gene, hgvsp, etc.)
    """
    entries = info_csq.split(",")
    results = []

    # Determine field indices with defaults
    idx_allele = field_map.get("Allele", 0)
    idx_consequence = field_map.get("Consequence", 1)
    idx_impact = field_map.get("IMPACT", 2)
    idx_gene = field_map.get("SYMBOL", 3)
    idx_hgvsp = field_map.get("HGVSp", 11)
    idx_amino_acids = field_map.get("Amino_acids", 15)
    idx_protein_pos = field_map.get("Protein_position", 14)

    for i, entry in enumerate(entries):
        fields = entry.split("|")

        # Skip if too few fields
        if len(fields) <= max(idx_consequence, idx_gene, idx_impact):
            logger.debug(f"CSQ entry {i}: skipping (only {len(fields)} fields)")
            continue

        consequence = fields[idx_consequence].strip() if idx_consequence < len(fields) else ""
        gene = fields[idx_gene].strip() if idx_gene < len(fields) else ""
        impact = fields[idx_impact].strip() if idx_impact < len(fields) else ""
        allele = fields[idx_allele].strip() if idx_allele < len(fields) else ""
        hgvsp = fields[idx_hgvsp].strip() if idx_hgvsp < len(fields) else ""
        amino_acids = fields[idx_amino_acids].strip() if idx_amino_acids < len(fields) else ""
        protein_pos = fields[idx_protein_pos].strip() if idx_protein_pos < len(fields) else ""

        results.append({
            "allele": allele,
            "consequence": consequence,
            "impact": impact,
            "gene": gene,
            "hgvsp": hgvsp,
            "amino_acids": amino_acids,
            "protein_pos": protein_pos,
        })

    return results


def _parse_snpeff_ann(info_ann: str) -> list[dict]:
    """
    Parse SnpEff ANN field. Format: Allele|Annotation|Impact|Gene|...
    Field order: 0=Allele, 1=Annotation, 2=Impact, 3=Gene, 4=GeneID, 5=Feature,
                 6=FeatureID, 7=TranscriptBiotype, 8=HGVS.c, 9=HGVS.p, 10=cDNA.pos/cDNA.length,
                 11=CDS.pos/CDS.length, 12=Protein.pos/Protein.length, 13=Distance
    """
    entries = info_ann.split(",")
    results = []
    for entry in entries:
        fields = entry.split("|")
        if len(fields) < 10:
            continue

        allele = fields[0].strip() if len(fields) > 0 else ""
        consequence = fields[1].strip() if len(fields) > 1 else ""
        impact = fields[2].strip() if len(fields) > 2 else ""
        gene = fields[3].strip() if len(fields) > 3 else ""
        hgvsp = fields[9].strip() if len(fields) > 9 else ""
        # For protein position, extract from "pos/length" format
        protein_pos = ""
        if len(fields) > 12:
            protein_info = fields[12].strip()
            if "/" in protein_info:
                protein_pos = protein_info.split("/")[0]

        results.append({
            "allele": allele,
            "consequence": consequence,
            "impact": impact,
            "gene": gene,
            "hgvsp": hgvsp,
            "amino_acids": "",  # Not available in SnpEff ANN
            "protein_pos": protein_pos,
        })
    return results


def _extract_vaf(variant) -> Optional[float]:
    """
    Try to get VAF from common VCF fields.
    Checks: INFO/AF, FORMAT/AF (tumor sample), computed from FORMAT/AD.

    For FORMAT fields: tries last sample first (tumor in DRAGEN, usually index -1),
    then falls back to first sample (normal in paired calling, index 0).
    """
    # INFO/AF (Mutect2 style, global allele frequency)
    try:
        af = variant.INFO.get("AF")
        if af is not None:
            # af can be a tuple for multi-allelic; take first
            if isinstance(af, (list, tuple)):
                return float(af[0])
            return float(af)
    except (TypeError, ValueError):
        pass

    # FORMAT/AF (per-sample allele frequency)
    # Try last sample first (tumor), then first (normal)
    try:
        af_fmt = variant.format("AF")
        if af_fmt is not None and len(af_fmt) > 0:
            # Try last sample first (usually tumor in DRAGEN)
            for sample_idx in [-1, 0]:
                try:
                    if af_fmt.ndim > 1:
                        val = af_fmt[sample_idx][0]
                    else:
                        val = af_fmt[sample_idx]
                    if val is not None and val > 0:
                        return float(val)
                except (IndexError, TypeError, ValueError):
                    continue
    except (TypeError, ValueError, IndexError, AttributeError):
        pass

    # Compute from FORMAT/AD (allelic depths)
    # Try last sample first (tumor), then first (normal)
    try:
        ad = variant.format("AD")
        if ad is not None and len(ad) > 0:
            # Try last sample (tumor) first, then first (normal)
            for sample_idx in [-1, 0]:
                try:
                    if ad.ndim > 1:
                        ref_depth = int(ad[sample_idx][0])
                        alt_depth = int(ad[sample_idx][1])
                    else:
                        ref_depth = int(ad[0])
                        alt_depth = int(ad[1])
                    total = ref_depth + alt_depth
                    if total > 0:
                        return round(alt_depth / total, 4)
                except (IndexError, TypeError, ValueError):
                    continue
    except (TypeError, ValueError, IndexError, AttributeError):
        pass

    return None


def _normalize_chrom(chrom: str) -> str:
    """Ensure chromosome has 'chr' prefix for GRCh38 consistency."""
    if not chrom.startswith("chr"):
        return f"chr{chrom}"
    return chrom


def _extract_protein_change(hgvsp: str, amino_acids: str, protein_pos: str) -> Optional[str]:
    """
    Extract protein change notation from available fields.

    Priority:
    1. HGVSp field (preferred, e.g., "p.V600E" or "ENSP123:p.V600E")
    2. Reconstruct from Amino_acids + Protein_position (e.g., "V/E" + "600" -> "p.V600E")

    Args:
        hgvsp: HGVS protein notation
        amino_acids: Ref/Alt amino acids, e.g., "V/E"
        protein_pos: Protein position, e.g., "600"

    Returns:
        Protein change string or None
    """
    if hgvsp:
        # HGVSp format: ENSP00000...:p.Val600Glu or just p.V600E
        if ":" in hgvsp:
            return hgvsp.split(":")[-1]
        else:
            return hgvsp

    # Fallback: reconstruct from Amino_acids and Protein_position
    if amino_acids and protein_pos:
        # Amino_acids format: "V/E" or "Val/Glu"
        parts = amino_acids.split("/")
        if len(parts) == 2:
            ref_aa = parts[0].strip()
            alt_aa = parts[1].strip()
            pos = protein_pos.strip()

            # Short form (single letter codes)
            if len(ref_aa) == 1 and len(alt_aa) == 1:
                return f"p.{ref_aa}{pos}{alt_aa}"
            # Long form (three letter codes) -> convert to short
            elif len(ref_aa) == 3 and len(alt_aa) == 3:
                aa_3_to_1 = {
                    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
                    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
                    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
                    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
                }
                ref_1 = aa_3_to_1.get(ref_aa.upper(), "?")
                alt_1 = aa_3_to_1.get(alt_aa.upper(), "?")
                return f"p.{ref_1}{pos}{alt_1}"

    return None


def parse_vcf(vcf_path: str | Path, min_vaf: float = 0.0) -> ParseResult:
    """
    Parse an annotated VCF and extract coding somatic variants.

    Args:
        vcf_path: Path to VCF or VCF.gz file
        min_vaf: Minimum variant allele frequency to include (0.0 = no filter)

    Returns:
        ParseResult with variants list and metadata about skipped records.
    """
    vcf_path = str(vcf_path)
    vcf = VCF(vcf_path)
    variants = []
    total = 0
    skipped_filter = 0
    skipped_noncoding = 0
    skipped_vaf = 0
    consequence_counts: dict[str, int] = {}
    consequence_terms_seen = {}  # Track all consequence terms

    # Parse CSQ header to map field names to indices
    csq_field_map = _parse_csq_format_header(vcf)

    for record in vcf:
        total += 1

        # Skip filtered variants (keep PASS and '.' / no filter)
        filt = record.FILTER
        if filt is not None and filt != "PASS" and filt != ".":
            skipped_filter += 1
            continue

        # Get annotation from VEP CSQ or SnpEff ANN
        csq = record.INFO.get("CSQ")
        ann = record.INFO.get("ANN")
        func = record.INFO.get("FUNCOTATION")

        annotations = []
        if csq:
            annotations = _parse_vep_csq(csq, csq_field_map)
        elif ann:
            annotations = _parse_snpeff_ann(ann)
        elif func:
            # Funcotator: simplified handling, just grab gene from first field
            # Format: [gene|...]
            parts = str(func).strip("[]").split("|")
            if len(parts) >= 2:
                annotations = [{
                    "allele": "",
                    "consequence": "missense_variant",
                    "gene": parts[0],
                    "hgvsp": "",
                    "impact": "",
                    "amino_acids": "",
                    "protein_pos": "",
                }]

        # Extract VAF
        vaf = _extract_vaf(record)
        if vaf is not None and vaf < min_vaf:
            skipped_vaf += 1
            continue

        # Handle each ALT allele separately.
        # VEP CSQ entries have the allele in field[0], so we match annotation
        # to the correct ALT. For single-allelic sites this is straightforward.
        any_coding = False
        for alt in record.ALT:
            alt_str = str(alt)

            # Find coding annotation matching this specific ALT allele.
            # VEP field[0] is the ALT allele; SnpEff field[0] is also the allele.
            # If no allele-specific match found, fall back to first coding annotation.
            coding_ann = None
            fallback_ann = None

            for a in annotations:
                consequence_str = a.get("consequence", "")
                consequence_terms = consequence_str.split("&")
                has_coding = False
                matched_term = None

                # Check if any consequence term is in our coding list (case-insensitive)
                for term in consequence_terms:
                    term_lower = term.strip().lower()
                    # Track all consequence terms seen
                    consequence_terms_seen[term_lower] = consequence_terms_seen.get(term_lower, 0) + 1

                    if term_lower in CODING_CONSEQUENCES:
                        has_coding = True
                        matched_term = term_lower
                        break

                if not has_coding:
                    continue

                # Check if this annotation's allele matches this ALT
                ann_allele = a.get("allele", "").strip()
                if ann_allele == alt_str:
                    coding_ann = dict(a)  # copy to avoid mutation
                    coding_ann["matched_consequence"] = matched_term
                    break
                elif fallback_ann is None:
                    fallback_ann = dict(a)
                    fallback_ann["matched_consequence"] = matched_term

            # Use allele-matched annotation, or fall back to first coding one
            if coding_ann is None:
                coding_ann = fallback_ann

            if not coding_ann:
                continue

            any_coding = True
            consequence = coding_ann["matched_consequence"]
            variant_type = VARIANT_TYPE_MAP.get(consequence, "missense")

            # Extract protein change: prefer HGVSp, fallback to Amino_acids + Protein_position
            hgvsp = coding_ann.get("hgvsp", "")
            amino_acids = coding_ann.get("amino_acids", "")
            protein_pos = coding_ann.get("protein_pos", "")
            protein_change = _extract_protein_change(hgvsp, amino_acids, protein_pos)

            parsed = ParsedVariant(
                chrom=_normalize_chrom(record.CHROM),
                pos=record.POS,
                ref=record.REF,
                alt=alt_str,
                gene=coding_ann.get("gene"),
                protein_change=protein_change,
                variant_type=variant_type,
                vaf=vaf,
                consequence=consequence,
                annotation={
                    "consequence": consequence,
                    "impact": coding_ann.get("impact", ""),
                    "hgvsp": hgvsp,
                },
            )
            variants.append(parsed)

        if not any_coding:
            skipped_noncoding += 1

    # Log summary
    logger.info(
        f"VCF parsed: {total} records, {len(variants)} coding variants kept. "
        f"Skipped: {skipped_filter} filtered, {skipped_noncoding} non-coding, "
        f"{skipped_vaf} low-VAF"
    )

    # Log all consequence terms seen
    if consequence_terms_seen:
        sorted_terms = sorted(consequence_terms_seen.items(), key=lambda x: -x[1])
        logger.info(f"Consequence terms encountered ({len(sorted_terms)} unique):")
        for term, count in sorted_terms[:20]:  # Log top 20
            in_coding = "CODING" if term in CODING_CONSEQUENCES else "non-coding"
            logger.info(f"  {term}: {count} ({in_coding})")
        if len(sorted_terms) > 20:
            logger.info(f"  ... and {len(sorted_terms) - 20} more")

    return ParseResult(
        variants=variants,
        total_records=total,
        skipped_filter=skipped_filter,
        skipped_noncoding=skipped_noncoding,
        skipped_vaf=skipped_vaf,
        consequence_counts=consequence_terms_seen,
    )
