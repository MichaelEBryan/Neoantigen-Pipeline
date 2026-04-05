"""
Pure utility functions for vaccine construct building.

No FastAPI, SQLAlchemy, or heavy deps here -- just ordering algorithms,
color assignment, and confidence tiers. Imported by both the construct
router and the unit tests.
"""
from typing import Optional
from collections import defaultdict


# -- Confidence tier --
# Duplicated threshold logic from epitopes router to keep this module standalone.

def confidence_tier(score: float, affinity_nm: float) -> str:
    if score >= 0.7 and affinity_nm <= 50:
        return "high"
    if score >= 0.4 and affinity_nm <= 500:
        return "medium"
    return "low"


# -- Gene color palette --
# Deterministic colors for gene regions. Works on light and dark backgrounds.

GENE_COLORS = [
    "#3b82f6",  # blue
    "#ef4444",  # red
    "#10b981",  # emerald
    "#f59e0b",  # amber
    "#8b5cf6",  # violet
    "#06b6d4",  # cyan
    "#ec4899",  # pink
    "#84cc16",  # lime
    "#f97316",  # orange
    "#6366f1",  # indigo
    "#14b8a6",  # teal
    "#e11d48",  # rose
]


def gene_color(gene: Optional[str], color_map: dict[str, str]) -> str:
    """Get a consistent color for a gene name."""
    if not gene:
        return "#94a3b8"  # slate for unknown
    if gene not in color_map:
        idx = len(color_map) % len(GENE_COLORS)
        color_map[gene] = GENE_COLORS[idx]
    return color_map[gene]


# -- Ordering algorithms --
# Each takes a list of objects with .immunogenicity_score (and optionally .gene)
# and returns a new ordered list.


def order_by_immunogenicity(epitopes: list) -> list:
    """Sort by immunogenicity score descending."""
    return sorted(epitopes, key=lambda e: e.immunogenicity_score, reverse=True)


def order_alternating_ends(epitopes: list) -> list:
    """
    Place strongest epitopes at alternating ends of the construct.
    Spreads high-scoring epitopes so immune response isn't concentrated.

    #1 -> pos 0, #2 -> pos N-1, #3 -> pos 1, #4 -> pos N-2, etc.
    """
    sorted_eps = sorted(epitopes, key=lambda e: e.immunogenicity_score, reverse=True)
    result = [None] * len(sorted_eps)
    left = 0
    right = len(sorted_eps) - 1

    for i, ep in enumerate(sorted_eps):
        if i % 2 == 0:
            result[left] = ep
            left += 1
        else:
            result[right] = ep
            right -= 1

    return [e for e in result if e is not None]


def order_gene_cluster(epitopes: list) -> list:
    """
    Group epitopes by gene, then sort within each group by immunogenicity.
    Reduces junctional neoepitopes from unrelated genes.
    """
    groups = defaultdict(list)
    for ep in epitopes:
        gene = getattr(ep, 'gene', None) or 'unknown'
        groups[gene].append(ep)

    result = []
    sorted_groups = sorted(
        groups.items(),
        key=lambda g: max(e.immunogenicity_score for e in g[1]),
        reverse=True,
    )
    for _gene, group_eps in sorted_groups:
        group_eps.sort(key=lambda e: e.immunogenicity_score, reverse=True)
        result.extend(group_eps)
    return result
