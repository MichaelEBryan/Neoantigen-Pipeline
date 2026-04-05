"""
Test script for 25mer flanking context extraction.

Validates that we can extract 25mer peptide context (12 aa flanking each
side of a mutation) from protein sequences, using both pyensembl lookup
and direct protein sequence input.

For vaccine construct design, we need the 25mer context to:
1. Show the mutation in its native protein context
2. Allow construct builders to use longer peptide sequences
3. Predict junctional cleavage accurately

The 25mer is: 12aa_left + mutant_residue + 12aa_right = 25 residues
For mutations near protein termini, the context is truncated accordingly.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.pipeline.peptide_gen import (
    _MISSENSE_RE,
    _to_single_letter,
    _get_protein_sequence,
    extract_25mer_context,
)


def test_extract_25mer_from_known_protein():
    """
    Test 25mer extraction using a known protein sequence.
    BRAF V600E: valine at position 600 -> glutamic acid.
    The 25mer should be 12 aa before + E + 12 aa after.
    """
    # Simulated BRAF protein region around position 600 (0-based index 599)
    # Real BRAF: ...DLNHIRISTSDFGL... V HSITAPRSIPRS...
    # We use a synthetic but representative sequence for offline testing
    fake_protein = "A" * 588 + "DLNHIRISTSDFGL" + "V" + "HSITAPRSIPRS" + "A" * 100
    # Position 600 (1-based) = index 599+14 = 602 in this fake. Let's be exact:
    # Actually let's just use position 603 as the V (index 602)
    # Better: construct so position 600 (1-based) = index 599
    fake_protein = "A" * 587 + "DLNHIRISTSDFGL" + "V" + "HSITAPRSIPRS" + "A" * 100
    # V is at index 587 + 14 = 601 (0-based), which is position 602 (1-based)
    # Let me just be explicit:

    # Build a protein where the V is exactly at 1-based position 600
    # 1-based pos 600 = 0-based index 599, so prefix must be 599 chars
    prefix = "M" + "A" * 587 + "DLNHIRISTSD"  # 1 + 587 + 11 = 599 residues
    mut_site = "V"                               # index 599 (0-based) = position 600 (1-based)
    suffix = "HSITAPRSIPRSAAA" + "G" * 100       # enough trailing context
    fake_protein = prefix + mut_site + suffix

    assert fake_protein[599] == "V", f"Expected V at index 599, got {fake_protein[599]}"

    # Extract 25mer context for p.V600E
    context = extract_25mer_context(
        protein_seq=fake_protein,
        protein_change="p.V600E",
        variant_type="missense",
    )

    assert context is not None, "Should return a context dict"
    assert len(context["wt_25mer"]) == 25, f"WT 25mer should be 25 aa, got {len(context['wt_25mer'])}"
    assert len(context["mut_25mer"]) == 25, f"Mut 25mer should be 25 aa, got {len(context['mut_25mer'])}"

    # The WT 25mer should have V at the center (position 12, 0-based)
    assert context["wt_25mer"][12] == "V", f"WT center should be V, got {context['wt_25mer'][12]}"
    # The mutant 25mer should have E at the center
    assert context["mut_25mer"][12] == "E", f"Mut center should be E, got {context['mut_25mer'][12]}"
    # The flanking sequences should be identical between WT and mutant
    assert context["wt_25mer"][:12] == context["mut_25mer"][:12], "Left flank should match"
    assert context["wt_25mer"][13:] == context["mut_25mer"][13:], "Right flank should match"

    print(f"WT  25mer: {context['wt_25mer']}")
    print(f"MUT 25mer: {context['mut_25mer']}")
    print(f"Mutation position in 25mer: {context['mutation_position']}")
    print("PASS: test_extract_25mer_from_known_protein")


def test_25mer_near_n_terminus():
    """
    Test 25mer extraction when mutation is near the N-terminus.
    Position 5 (1-based) means only 4 residues of left context.
    """
    protein = "MARVL" + "Q" + "RSTAPLNIHKDECFG" + "A" * 50
    # Q is at position 6 (1-based), index 5
    # With 12 aa left context, we'd need index -7 which is impossible
    # Should truncate to available context

    context = extract_25mer_context(
        protein_seq=protein,
        protein_change="p.Q6R",
        variant_type="missense",
    )

    assert context is not None
    # Left context is only 5 residues (MARVL), not 12
    # Total length = 5 + 1 + 12 = 18 (less than 25)
    assert len(context["mut_25mer"]) < 25, "Should be truncated near N-terminus"
    assert len(context["mut_25mer"]) == 18, f"Expected 18 aa, got {len(context['mut_25mer'])}"
    assert context["mutation_position"] == 5, f"Mut pos should be 5, got {context['mutation_position']}"
    assert context["mut_25mer"][5] == "R", f"Mutant AA should be R at pos 5, got {context['mut_25mer'][5]}"

    print(f"N-term 25mer: {context['mut_25mer']} (length {len(context['mut_25mer'])})")
    print("PASS: test_25mer_near_n_terminus")


def test_25mer_near_c_terminus():
    """
    Test 25mer extraction when mutation is near the C-terminus.
    """
    protein = "A" * 50 + "DLNHIRISTSD" + "V" + "HK"
    # V is at index 61, protein length 64
    # Only 2 residues of right context (HK)

    pos_1based = 62
    context = extract_25mer_context(
        protein_seq=protein,
        protein_change=f"p.V{pos_1based}E",
        variant_type="missense",
    )

    assert context is not None
    # Right context is only 2 residues, total = 12 + 1 + 2 = 15
    assert len(context["mut_25mer"]) == 15, f"Expected 15 aa, got {len(context['mut_25mer'])}"
    assert context["mut_25mer"][12] == "E", f"Mutant AA should be E, got {context['mut_25mer'][12]}"

    print(f"C-term 25mer: {context['mut_25mer']} (length {len(context['mut_25mer'])})")
    print("PASS: test_25mer_near_c_terminus")


def test_25mer_ref_aa_mismatch():
    """
    If the reference AA in the HGVS doesn't match the protein, we should
    still extract context but log a warning. This can happen with transcript
    version mismatches.
    """
    protein = "A" * 10 + "K" + "A" * 50  # K at position 11 (1-based)

    # Claim it's V11E but protein has K at that position
    context = extract_25mer_context(
        protein_seq=protein,
        protein_change="p.V11E",
        variant_type="missense",
    )

    # Should still return context (with the actual protein context)
    # but mark the mismatch
    assert context is not None
    assert context.get("ref_mismatch") == True, "Should flag ref AA mismatch"

    print(f"Mismatch 25mer: {context['mut_25mer']}")
    print("PASS: test_25mer_ref_aa_mismatch")


def test_25mer_unparseable_hgvs():
    """
    If the protein change can't be parsed, return None gracefully.
    """
    context = extract_25mer_context(
        protein_seq="MARVLQRSTAPLNIHKDECFG",
        protein_change="p.?",
        variant_type="missense",
    )
    assert context is None, "Should return None for unparseable HGVS"
    print("PASS: test_25mer_unparseable_hgvs")


def test_25mer_frameshift():
    """
    For frameshifts, the 25mer should show the WT sequence up to the
    frameshift point. The mutant sequence downstream is unknown without
    translation, so we show WT context only.
    """
    protein = "A" * 50 + "DLNHIRISTSD" + "R" + "PLKGHYNWQF" + "A" * 50
    # R at index 61, position 62 (1-based)

    context = extract_25mer_context(
        protein_seq=protein,
        protein_change="p.R62fs",
        variant_type="frameshift",
    )

    assert context is not None
    assert context["wt_25mer"] is not None
    # For frameshifts, mut_25mer may be None or marked as truncated
    # since we don't know the novel downstream sequence
    print(f"Frameshift WT 25mer: {context['wt_25mer']}")
    print("PASS: test_25mer_frameshift")


if __name__ == "__main__":
    test_extract_25mer_from_known_protein()
    test_25mer_near_n_terminus()
    test_25mer_near_c_terminus()
    test_25mer_ref_aa_mismatch()
    test_25mer_unparseable_hgvs()
    test_25mer_frameshift()
    print("\n=== ALL 25mer EXTRACTION TESTS PASSED ===")
