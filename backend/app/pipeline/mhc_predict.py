"""
MHC Class I binding prediction via MHCflurry 2.0.

Uses the Class1PresentationPredictor which gives:
- binding affinity (IC50 in nM, lower = stronger)
- presentation score (0-1, probability of being presented on cell surface)
- processing score (0-1, probability of proteasomal processing + TAP transport)

The predictor is loaded once and reused. It handles ~7000 predictions/sec
on CPU, so even 100k peptide-allele pairs finish in ~15 seconds.

For environments where MHCflurry isn't installed (CI, lightweight dev),
a MockPredictor generates plausible random scores.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MHCPrediction:
    """Result of MHC binding prediction for one peptide-allele pair."""
    peptide_seq: str
    hla_allele: str
    binding_affinity_nm: float    # IC50 in nM (lower = stronger binder)
    presentation_score: float     # 0-1
    processing_score: float       # 0-1


class BaseMHCPredictor(ABC):
    """Interface for MHC binding predictors."""

    @abstractmethod
    def predict(
        self,
        peptides: list[str],
        alleles: list[str],
    ) -> list[MHCPrediction]:
        """
        Predict binding for all peptide-allele combinations.

        Args:
            peptides: List of peptide sequences (8-11mers)
            alleles: List of HLA alleles (e.g. ["HLA-A*02:01", "HLA-B*44:02"])

        Returns:
            One MHCPrediction per peptide-allele pair.
            Total results = len(peptides) * len(alleles)
        """
        ...


class MHCflurryPredictor(BaseMHCPredictor):
    """
    Real MHCflurry 2.0 predictor.

    Loads the presentation model (includes binding + processing + presentation).
    Model data must be downloaded first: `mhcflurry-downloads fetch models_class1_presentation`
    """

    def __init__(self):
        self._predictor = None

    def _load(self):
        """Lazy-load the predictor (heavy, ~2GB models)."""
        if self._predictor is None:
            from mhcflurry import Class1PresentationPredictor
            logger.info("Loading MHCflurry presentation predictor...")
            self._predictor = Class1PresentationPredictor.load()
            logger.info("MHCflurry loaded.")

    def predict(
        self,
        peptides: list[str],
        alleles: list[str],
    ) -> list[MHCPrediction]:
        self._load()

        if not peptides or not alleles:
            return []

        # MHCflurry's predict() takes parallel lists of peptides and alleles.
        # For all-vs-all, we need to expand into the cartesian product.
        expanded_peptides = []
        expanded_alleles = []
        for pep in peptides:
            for allele in alleles:
                expanded_peptides.append(pep)
                expanded_alleles.append(allele)

        logger.info(
            f"Running MHCflurry: {len(peptides)} peptides x {len(alleles)} alleles "
            f"= {len(expanded_peptides)} predictions"
        )

        df = self._predictor.predict(
            peptides=expanded_peptides,
            alleles=expanded_alleles,
            verbose=0,
        )

        # Resolve column names upfront. MHCflurry has used different names
        # across versions: "affinity" vs "mhcflurry_affinity", etc.
        # We fail hard if none of the known names are present -- silent
        # fallback to dummy values would produce garbage scores.
        cols = set(df.columns)

        def _resolve_col(candidates: list[str], label: str) -> str:
            for c in candidates:
                if c in cols:
                    return c
            raise KeyError(
                f"MHCflurry output missing {label} column. "
                f"Expected one of {candidates}, got columns: {sorted(cols)}"
            )

        col_affinity = _resolve_col(["affinity", "mhcflurry_affinity"], "affinity")
        col_presentation = _resolve_col(
            ["presentation_score", "mhcflurry_presentation_score"], "presentation"
        )
        col_processing = _resolve_col(
            ["processing_score", "mhcflurry_processing_score"], "processing"
        )

        logger.info(
            f"MHCflurry columns resolved: affinity={col_affinity}, "
            f"presentation={col_presentation}, processing={col_processing}"
        )

        results = []
        for _, row in df.iterrows():
            results.append(MHCPrediction(
                peptide_seq=row["peptide"],
                hla_allele=row["allele"],
                binding_affinity_nm=float(row[col_affinity]),
                presentation_score=float(row[col_presentation]),
                processing_score=float(row[col_processing]),
            ))

        logger.info(f"MHCflurry completed: {len(results)} predictions")
        return results


class MockMHCPredictor(BaseMHCPredictor):
    """
    Mock predictor for testing and dev environments.

    Generates deterministic scores based on peptide sequence hash.
    Scores are realistic-looking but not biologically meaningful.
    """

    def predict(
        self,
        peptides: list[str],
        alleles: list[str],
    ) -> list[MHCPrediction]:
        import hashlib

        results = []
        for pep in peptides:
            for allele in alleles:
                # Deterministic hash -> score mapping
                h = hashlib.md5(f"{pep}:{allele}".encode()).hexdigest()
                hash_val = int(h[:8], 16) / 0xFFFFFFFF  # 0-1

                # Map to realistic ranges
                # Strong binders: IC50 < 50nM (~5% of peptides)
                # Weak binders: 50-500nM (~15%)
                # Non-binders: >500nM (~80%)
                affinity = 10 ** (1 + hash_val * 4)  # 10 to 100000 nM
                presentation = max(0.0, 1.0 - (hash_val * 1.2))
                processing = max(0.0, min(1.0, 0.9 - hash_val * 0.8))

                results.append(MHCPrediction(
                    peptide_seq=pep,
                    hla_allele=allele,
                    binding_affinity_nm=round(affinity, 1),
                    presentation_score=round(presentation, 4),
                    processing_score=round(processing, 4),
                ))

        logger.info(f"MockMHCPredictor: {len(results)} predictions (NOT real scores)")
        return results


def get_predictor(use_mock: bool = False) -> BaseMHCPredictor:
    """
    Factory: return real MHCflurry predictor or mock.

    Automatically falls back to mock if MHCflurry can't be loaded.
    """
    if use_mock:
        return MockMHCPredictor()

    try:
        predictor = MHCflurryPredictor()
        # Don't load yet (lazy), just verify import works
        import mhcflurry  # noqa: F401
        return predictor
    except ImportError:
        logger.warning("MHCflurry not available, using MockMHCPredictor")
        return MockMHCPredictor()
