"""
CVDash VCF-to-epitope pipeline.

Modules:
    vcf_parser   - Parse annotated VCF, extract coding variants
    peptide_gen  - Generate overlapping 8-11mer mutant peptides
    mhc_predict  - MHCflurry 2.0 binding/presentation predictions
    scorer       - Composite immunogenicity scoring (6 components)
    orchestrator - Wire everything together, write results to DB
"""
