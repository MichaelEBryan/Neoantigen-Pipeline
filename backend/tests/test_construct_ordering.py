"""
Tests for construct builder ordering algorithms and helper functions.

Pure logic tests -- no DB, no HTTP, no FastAPI. Validates that:
1. Ordering strategies produce correct epitope order
2. Gene color assignment is deterministic
3. Confidence tier classification matches expected thresholds
4. Edge cases (single epitope, all same gene, etc.) are handled

Imports from app.construct_utils (lightweight, no heavy deps).
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock
from app.construct_utils import (
    order_by_immunogenicity,
    order_alternating_ends,
    gene_color,
    confidence_tier,
    GENE_COLORS,
)


def _make_ep(score: float, gene: str = "BRAF"):
    """Create a minimal mock epitope with immunogenicity_score and gene."""
    ep = MagicMock()
    ep.immunogenicity_score = score
    ep.gene = gene
    return ep


# -- Immunogenicity ordering --

def test_immunogenicity_descending():
    """Epitopes should be sorted highest score first."""
    eps = [_make_ep(0.3), _make_ep(0.9), _make_ep(0.5)]
    ordered = order_by_immunogenicity(eps)
    scores = [e.immunogenicity_score for e in ordered]
    assert scores == [0.9, 0.5, 0.3], f"Expected descending, got {scores}"
    print("PASS: test_immunogenicity_descending")


def test_immunogenicity_single():
    """Single epitope should just come back as-is."""
    eps = [_make_ep(0.7)]
    ordered = order_by_immunogenicity(eps)
    assert len(ordered) == 1
    assert ordered[0].immunogenicity_score == 0.7
    print("PASS: test_immunogenicity_single")


def test_immunogenicity_tie():
    """Equal scores should not crash. Order is stable (Python sort)."""
    eps = [_make_ep(0.5), _make_ep(0.5), _make_ep(0.5)]
    ordered = order_by_immunogenicity(eps)
    assert len(ordered) == 3
    print("PASS: test_immunogenicity_tie")


# -- Alternating ends ordering --

def test_alternating_basic():
    """
    Alternating placement: #1 -> pos 0, #2 -> pos N-1, #3 -> pos 1, etc.
    With scores [0.9, 0.7, 0.5, 0.3], expect:
      pos 0: 0.9, pos 1: 0.5, pos 2: 0.3, pos 3: 0.7
    """
    eps = [_make_ep(0.5), _make_ep(0.3), _make_ep(0.9), _make_ep(0.7)]
    ordered = order_alternating_ends(eps)
    scores = [e.immunogenicity_score for e in ordered]
    assert scores == [0.9, 0.5, 0.3, 0.7], f"Expected [0.9, 0.5, 0.3, 0.7], got {scores}"
    print("PASS: test_alternating_basic")


def test_alternating_two():
    """Two epitopes: strongest first, second last."""
    eps = [_make_ep(0.3), _make_ep(0.8)]
    ordered = order_alternating_ends(eps)
    scores = [e.immunogenicity_score for e in ordered]
    assert scores == [0.8, 0.3], f"Expected [0.8, 0.3], got {scores}"
    print("PASS: test_alternating_two")


def test_alternating_single():
    """Single epitope returns as-is."""
    eps = [_make_ep(0.6)]
    ordered = order_alternating_ends(eps)
    assert len(ordered) == 1
    print("PASS: test_alternating_single")


def test_alternating_five():
    """
    5 epitopes with scores [0.9, 0.8, 0.7, 0.6, 0.5]:
    #1 (0.9) -> pos 0, #2 (0.8) -> pos 4, #3 (0.7) -> pos 1,
    #4 (0.6) -> pos 3, #5 (0.5) -> pos 2
    Result: [0.9, 0.7, 0.5, 0.6, 0.8]
    """
    eps = [_make_ep(s) for s in [0.5, 0.6, 0.7, 0.8, 0.9]]
    ordered = order_alternating_ends(eps)
    scores = [e.immunogenicity_score for e in ordered]
    assert scores == [0.9, 0.7, 0.5, 0.6, 0.8], f"Expected [0.9, 0.7, 0.5, 0.6, 0.8], got {scores}"
    print("PASS: test_alternating_five")


# -- Gene color assignment --

def test_gene_color_deterministic():
    """Same gene should always get the same color."""
    cm = {}
    c1 = gene_color("BRAF", cm)
    c2 = gene_color("BRAF", cm)
    assert c1 == c2, "Same gene should return same color"
    assert c1 == GENE_COLORS[0], f"First gene should get first color, got {c1}"
    print("PASS: test_gene_color_deterministic")


def test_gene_color_none():
    """None gene should get slate fallback."""
    cm = {}
    c = gene_color(None, cm)
    assert c == "#94a3b8", f"None gene should be slate, got {c}"
    print("PASS: test_gene_color_none")


def test_gene_color_multiple():
    """Different genes get different colors (until palette wraps)."""
    cm = {}
    genes = ["BRAF", "TP53", "KRAS", "EGFR"]
    colors = [gene_color(g, cm) for g in genes]
    assert len(set(colors)) == 4, f"Expected 4 unique colors, got {colors}"
    print("PASS: test_gene_color_multiple")


def test_gene_color_wraps():
    """When we have more genes than palette colors, colors wrap around."""
    cm = {}
    n = len(GENE_COLORS) + 2
    genes = [f"GENE_{i}" for i in range(n)]
    colors = [gene_color(g, cm) for g in genes]
    assert colors[0] == colors[len(GENE_COLORS)], "Should wrap around palette"
    print("PASS: test_gene_color_wraps")


# -- Confidence tier --

def test_confidence_high():
    assert confidence_tier(0.8, 30) == "high"
    assert confidence_tier(0.7, 50) == "high"
    print("PASS: test_confidence_high")


def test_confidence_medium():
    assert confidence_tier(0.5, 200) == "medium"
    assert confidence_tier(0.4, 500) == "medium"
    print("PASS: test_confidence_medium")


def test_confidence_low():
    assert confidence_tier(0.3, 600) == "low"
    assert confidence_tier(0.1, 1000) == "low"
    # Good score but affinity > 500: fails medium threshold on affinity
    assert confidence_tier(0.8, 600) == "low"
    # Good affinity but score < 0.4: fails medium threshold on score
    assert confidence_tier(0.3, 30) == "low"
    print("PASS: test_confidence_low")


def test_confidence_boundary():
    """Exact boundary values."""
    assert confidence_tier(0.7, 50) == "high"
    assert confidence_tier(0.7, 51) == "medium"
    assert confidence_tier(0.69, 50) == "medium"
    assert confidence_tier(0.4, 500) == "medium"
    assert confidence_tier(0.4, 501) == "low"
    assert confidence_tier(0.39, 500) == "low"
    print("PASS: test_confidence_boundary")


if __name__ == "__main__":
    test_immunogenicity_descending()
    test_immunogenicity_single()
    test_immunogenicity_tie()
    test_alternating_basic()
    test_alternating_two()
    test_alternating_single()
    test_alternating_five()
    test_gene_color_deterministic()
    test_gene_color_none()
    test_gene_color_multiple()
    test_gene_color_wraps()
    test_confidence_high()
    test_confidence_medium()
    test_confidence_low()
    test_confidence_boundary()
    print("\n=== ALL CONSTRUCT ORDERING TESTS PASSED ===")
