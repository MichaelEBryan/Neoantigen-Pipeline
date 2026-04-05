"""
Mutant peptide generator.

For each somatic coding variant, generates overlapping 8-11mer peptide
windows that span the mutation site. These are the candidate neoepitopes
that get scored by MHCflurry.

Strategy:
- For missense: single AA substitution in the protein sequence.
  Generate all windows of length 8-11 that contain the mutant residue.
- For frameshift: the entire downstream sequence after the frameshift
  is novel. Generate windows starting from the frameshift position.
- For inframe indels: similar to missense but may insert/delete residues.

We use pyensembl for transcript/protein lookup when available.
Falls back to a codon-table approach when pyensembl data isn't installed.

Assumptions:
- Input variants have gene name and protein_change (from VCF annotation).
- protein_change is in HGVS format like p.V600E, p.R248W, etc.
- For frameshifts, we generate a fixed window of novel sequence.
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional

from .vcf_parser import ParsedVariant

logger = logging.getLogger(__name__)

# Peptide lengths to generate (MHC Class I binding groove: 8-11 residues)
PEPTIDE_LENGTHS = [8, 9, 10, 11]

# How many novel residues to consider downstream of a frameshift
FRAMESHIFT_WINDOW = 30


@dataclass
class CandidatePeptide:
    """A candidate neoepitope peptide to be scored."""
    peptide_seq: str
    peptide_length: int
    variant: ParsedVariant       # back-reference to source variant
    mutation_position: int       # 0-based position of mutant residue in peptide
    is_mutant: bool = True       # always True for candidates, False for WT comparison


# -- HGVS protein change parsing --

# Matches: p.V600E, p.Val600Glu, p.R248W, p.Arg248Trp
_MISSENSE_RE = re.compile(
    r"p\.([A-Z][a-z]{0,2})(\d+)([A-Z][a-z]{0,2})"
)

# Matches: p.L747_A750del, p.N771_H773dup
_INFRAME_DEL_RE = re.compile(r"p\.\w+(\d+)_\w+(\d+)del")
_INFRAME_INS_RE = re.compile(r"p\.\w+(\d+)_\w+(\d+)ins(\w+)")

# Matches: p.T790Mfs*30, p.R248fs
_FRAMESHIFT_RE = re.compile(r"p\.([A-Z][a-z]{0,2})(\d+)\w*fs")

# Standard 3-letter to 1-letter AA code
AA3_TO_1 = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C",
    "Gln": "Q", "Glu": "E", "Gly": "G", "His": "H", "Ile": "I",
    "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F", "Pro": "P",
    "Ser": "S", "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V",
    "Ter": "*",
}


def _to_single_letter(aa: str) -> str:
    """Convert 1-letter or 3-letter AA code to 1-letter."""
    if len(aa) == 1:
        return aa
    return AA3_TO_1.get(aa, aa[0].upper())


# Module-level cache for EnsemblRelease. Created once, reused across calls.
# This avoids calling download() + index() on every protein lookup.
_ensembl_cache: Optional[object] = None


def _get_ensembl():
    """Get (or create) the cached EnsemblRelease instance."""
    global _ensembl_cache
    if _ensembl_cache is not None:
        return _ensembl_cache
    try:
        from pyensembl import EnsemblRelease
        ensembl = EnsemblRelease(110)
        # download() and index() are no-ops if data already exists on disk
        ensembl.download()
        ensembl.index()
        _ensembl_cache = ensembl
        return ensembl
    except Exception as e:
        logger.warning(f"pyensembl init failed: {e}")
        return None


def _get_protein_sequence(gene: str, transcript_id: Optional[str] = None) -> Optional[str]:
    """
    Fetch protein sequence for a gene using pyensembl.
    Returns None if pyensembl data isn't available.
    """
    try:
        ensembl = _get_ensembl()
        if ensembl is None:
            return None

        if transcript_id:
            transcript = ensembl.transcript_by_id(transcript_id)
            return transcript.protein_sequence
        else:
            # Get canonical transcript for gene
            gene_obj = ensembl.genes_by_name(gene)
            if not gene_obj:
                return None
            # Take longest transcript as proxy for canonical
            transcripts = ensembl.transcripts_by_gene_name(gene)
            best = max(transcripts, key=lambda t: len(t.protein_sequence or ""))
            return best.protein_sequence
    except Exception as e:
        logger.debug(f"pyensembl lookup failed for {gene}: {e}")
        return None


def extract_25mer_context(
    protein_seq: str,
    protein_change: str,
    variant_type: str,
    flank_size: int = 12,
) -> Optional[dict]:
    """
    Extract a 25mer (or shorter near termini) centered on the mutation site.

    For vaccine construct design, the 25mer provides biological context
    around each neoepitope. The standard window is 12 aa flanking each
    side of the mutant residue, giving a 25-residue peptide.

    Args:
        protein_seq: Full wild-type protein sequence (from pyensembl).
        protein_change: HGVS notation, e.g. "p.V600E" or "p.R248fs".
        variant_type: "missense", "frameshift", "inframe_indel", "nonsense".
        flank_size: Number of flanking residues each side (default 12 for 25mer).

    Returns:
        Dict with keys:
          - wt_25mer: wild-type context sequence
          - mut_25mer: mutant context sequence (None for frameshifts)
          - mutation_position: 0-based index of mutation in the context
          - ref_aa: reference amino acid at mutation site
          - alt_aa: alternate amino acid (None for frameshifts)
          - protein_position: 1-based position in full protein
          - ref_mismatch: True if ref AA in HGVS doesn't match protein
        Returns None if protein_change can't be parsed.
    """
    if not protein_seq or not protein_change:
        return None

    ref_mismatch = False

    if variant_type == "frameshift":
        match = _FRAMESHIFT_RE.match(protein_change)
        if not match:
            return None

        ref_aa = _to_single_letter(match.group(1))
        position = int(match.group(2))  # 1-based
        mut_idx = position - 1

        if mut_idx >= len(protein_seq):
            return None

        if protein_seq[mut_idx] != ref_aa:
            ref_mismatch = True

        left_start = max(0, mut_idx - flank_size)
        right_end = min(len(protein_seq), mut_idx + flank_size + 1)
        wt_context = protein_seq[left_start:right_end]
        mut_pos_in_context = mut_idx - left_start

        return {
            "wt_25mer": wt_context,
            "mut_25mer": None,  # can't determine novel downstream without translation
            "mutation_position": mut_pos_in_context,
            "ref_aa": ref_aa,
            "alt_aa": None,
            "protein_position": position,
            "ref_mismatch": ref_mismatch,
        }

    # Missense, inframe_indel, nonsense
    match = _MISSENSE_RE.match(protein_change)
    if not match:
        return None

    ref_aa = _to_single_letter(match.group(1))
    position = int(match.group(2))  # 1-based
    alt_aa = _to_single_letter(match.group(3))
    mut_idx = position - 1

    if mut_idx >= len(protein_seq):
        return None

    if protein_seq[mut_idx] != ref_aa:
        ref_mismatch = True
        logger.warning(
            f"25mer extraction: ref AA mismatch at position {position}: "
            f"HGVS says {ref_aa}, protein has {protein_seq[mut_idx]}"
        )

    # Extract flanking context
    left_start = max(0, mut_idx - flank_size)
    right_end = min(len(protein_seq), mut_idx + flank_size + 1)

    # Wild-type context
    wt_context = protein_seq[left_start:right_end]

    # Mutant context: substitute the alternate AA
    mutant_protein = protein_seq[:mut_idx] + alt_aa + protein_seq[mut_idx + 1:]
    mut_context = mutant_protein[left_start:right_end]

    # Position of mutation within the context string
    mut_pos_in_context = mut_idx - left_start

    return {
        "wt_25mer": wt_context,
        "mut_25mer": mut_context,
        "mutation_position": mut_pos_in_context,
        "ref_aa": ref_aa,
        "alt_aa": alt_aa,
        "protein_position": position,
        "ref_mismatch": ref_mismatch,
    }


def _generate_windows(sequence: str, mut_pos: int, lengths: list[int]) -> list[tuple[str, int]]:
    """
    Generate all overlapping peptide windows of given lengths
    that contain the residue at mut_pos.

    Returns list of (peptide_string, mutation_offset_in_peptide).
    """
    results = []
    seq_len = len(sequence)

    for pep_len in lengths:
        # Window start must be <= mut_pos and end must be > mut_pos
        # start ranges from max(0, mut_pos - pep_len + 1) to min(mut_pos, seq_len - pep_len)
        start_min = max(0, mut_pos - pep_len + 1)
        start_max = min(mut_pos, seq_len - pep_len)

        for start in range(start_min, start_max + 1):
            peptide = sequence[start:start + pep_len]
            if len(peptide) == pep_len:
                mut_offset = mut_pos - start
                results.append((peptide, mut_offset))

    return results


def generate_peptides_for_missense(
    variant: ParsedVariant,
    protein_seq: Optional[str] = None,
) -> list[CandidatePeptide]:
    """
    Generate candidate peptides for a missense variant.

    If protein_seq is provided, we mutate the actual protein sequence
    and extract windows. Otherwise, we use a minimal approach based on
    the HGVS protein change alone (synthetic flanking context).
    """
    match = _MISSENSE_RE.match(variant.protein_change or "")
    if not match:
        logger.warning(f"Cannot parse missense protein change: {variant.protein_change}")
        return []

    ref_aa = _to_single_letter(match.group(1))
    position = int(match.group(2))  # 1-based protein position
    alt_aa = _to_single_letter(match.group(3))

    # Position in 0-based index
    mut_idx = position - 1

    if protein_seq and mut_idx < len(protein_seq):
        # Verify the reference AA matches (sanity check)
        if protein_seq[mut_idx] != ref_aa:
            logger.warning(
                f"Reference AA mismatch at {variant.gene} pos {position}: "
                f"expected {ref_aa}, got {protein_seq[mut_idx]}. Using VCF annotation."
            )

        # Create mutant protein sequence
        mutant_seq = protein_seq[:mut_idx] + alt_aa + protein_seq[mut_idx + 1:]

        # Generate all overlapping windows containing the mutant residue
        windows = _generate_windows(mutant_seq, mut_idx, PEPTIDE_LENGTHS)
    else:
        # No protein sequence available. Generate a single peptide per length
        # centered on the mutant AA, using random-ish flanking residues derived
        # from common amino acid frequencies. This lets the pipeline produce
        # output even without pyensembl, though scores won't be biologically
        # accurate. In production, pyensembl should always be available.
        logger.info(f"No protein sequence for {variant.gene}, using synthetic flanking")

        # Use a fixed flanking sequence (common residues) so results are
        # deterministic and the peptides are valid AA strings for MHCflurry.
        # The flank is long enough for any window to contain real AAs.
        _FLANK = "AGLVSERKT"  # common residues in human proteome
        flank_needed = max(PEPTIDE_LENGTHS)
        left_flank = (_FLANK * (flank_needed // len(_FLANK) + 1))[:flank_needed]
        right_flank = (_FLANK * (flank_needed // len(_FLANK) + 1))[:flank_needed]
        synthetic = left_flank + alt_aa + right_flank
        mut_idx_synth = flank_needed
        windows = _generate_windows(synthetic, mut_idx_synth, PEPTIDE_LENGTHS)

    candidates = []
    seen = set()  # deduplicate identical peptides
    for peptide_seq, mut_offset in windows:
        if peptide_seq in seen:
            continue
        # Skip peptides containing stop codons
        if "*" in peptide_seq:
            continue
        seen.add(peptide_seq)
        candidates.append(CandidatePeptide(
            peptide_seq=peptide_seq,
            peptide_length=len(peptide_seq),
            variant=variant,
            mutation_position=mut_offset,
        ))

    return candidates


def generate_peptides_for_frameshift(
    variant: ParsedVariant,
    protein_seq: Optional[str] = None,
) -> list[CandidatePeptide]:
    """
    Generate candidate peptides for a frameshift variant.

    The entire sequence downstream of the frameshift is novel.
    We generate windows starting from the frameshift position.
    """
    match = _FRAMESHIFT_RE.match(variant.protein_change or "")
    if not match:
        logger.warning(f"Cannot parse frameshift protein change: {variant.protein_change}")
        return []

    position = int(match.group(2))  # 1-based
    mut_idx = position - 1

    # For frameshifts, ideally we'd have the novel downstream sequence from
    # the VCF + reference. For now, if we have the WT protein, we note where
    # the shift starts. The actual novel peptides would come from translating
    # the shifted reading frame -- this requires the actual nucleotide sequence.
    #
    # Simplified approach: generate windows from the WT protein at the
    # frameshift boundary. In production, the Isambard pipeline would
    # provide the full mutant protein via a translated sequence.

    if protein_seq and mut_idx < len(protein_seq):
        # Use sequence around the frameshift start as approximate context
        # The first residue at mut_idx is the last WT residue; everything after is novel
        start = max(0, mut_idx - max(PEPTIDE_LENGTHS))
        end = min(len(protein_seq), mut_idx + FRAMESHIFT_WINDOW)
        region = protein_seq[start:end]
        local_mut_idx = mut_idx - start

        windows = _generate_windows(region, local_mut_idx, PEPTIDE_LENGTHS)
    else:
        logger.info(f"No protein sequence for frameshift at {variant.gene}")
        return []

    candidates = []
    seen = set()
    for peptide_seq, mut_offset in windows:
        if peptide_seq in seen or "*" in peptide_seq:
            continue
        seen.add(peptide_seq)
        candidates.append(CandidatePeptide(
            peptide_seq=peptide_seq,
            peptide_length=len(peptide_seq),
            variant=variant,
            mutation_position=mut_offset,
        ))

    return candidates


def generate_peptides(
    variants: list[ParsedVariant],
    use_pyensembl: bool = True,
) -> list[CandidatePeptide]:
    """
    Generate candidate neoepitope peptides for all variants.

    Args:
        variants: Parsed variants from VCF
        use_pyensembl: Whether to fetch protein sequences via pyensembl.
                       Set False for testing or when data isn't installed.

    Returns:
        List of CandidatePeptide objects ready for MHCflurry scoring.
    """
    all_candidates = []
    protein_cache: dict[str, Optional[str]] = {}

    for variant in variants:
        # Fetch protein sequence (cached per gene)
        protein_seq = None
        if use_pyensembl and variant.gene:
            if variant.gene not in protein_cache:
                protein_cache[variant.gene] = _get_protein_sequence(variant.gene)
            protein_seq = protein_cache[variant.gene]

        if variant.variant_type == "missense":
            peptides = generate_peptides_for_missense(variant, protein_seq)
        elif variant.variant_type == "frameshift":
            peptides = generate_peptides_for_frameshift(variant, protein_seq)
        elif variant.variant_type in ("inframe_indel", "nonsense"):
            # For inframe indels and nonsense, use missense-like approach
            # (the protein change notation is similar)
            peptides = generate_peptides_for_missense(variant, protein_seq)
        else:
            logger.warning(f"Unknown variant type: {variant.variant_type}")
            peptides = []

        all_candidates.extend(peptides)

    logger.info(
        f"Generated {len(all_candidates)} candidate peptides from "
        f"{len(variants)} variants"
    )
    return all_candidates
