"""Initial schema -- all 8 tables for CVDash.

Revision ID: 001
Revises:
Create Date: 2026-04-01

Tables: users, projects, analyses, analysis_inputs, hla_types, variants, epitopes, job_logs
"""
from alembic import op
import sqlalchemy as sa


revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- users --
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("institution", sa.String(255), nullable=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # -- projects --
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("cancer_type", sa.String(255), nullable=False),
        sa.Column("stage", sa.String(10), nullable=True),
        sa.Column("reference_genome", sa.String(50), nullable=False, server_default="GRCh38"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])

    # -- analyses --
    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="queued"),
        sa.Column("input_type", sa.String(50), nullable=False),
        sa.Column("hla_provided", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("isambard_job_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_analyses_project_id", "analyses", ["project_id"])

    # -- analysis_inputs --
    op.create_table(
        "analysis_inputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_type", sa.String(50), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("checksum", sa.String(255), nullable=True),
    )
    op.create_index("ix_analysis_inputs_analysis_id", "analysis_inputs", ["analysis_id"])

    # -- hla_types --
    op.create_table(
        "hla_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("allele", sa.String(50), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
    )
    op.create_index("ix_hla_types_analysis_id", "hla_types", ["analysis_id"])

    # -- variants --
    op.create_table(
        "variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chrom", sa.String(50), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False),
        sa.Column("ref", sa.String(500), nullable=False),
        sa.Column("alt", sa.String(500), nullable=False),
        sa.Column("gene", sa.String(100), nullable=True),
        sa.Column("protein_change", sa.String(255), nullable=True),
        sa.Column("variant_type", sa.String(50), nullable=False),
        sa.Column("vaf", sa.Float(), nullable=True),
        sa.Column("annotation_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_variants_analysis_id", "variants", ["analysis_id"])
    op.create_index("ix_variants_gene", "variants", ["gene"])

    # -- epitopes --
    op.create_table(
        "epitopes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("peptide_seq", sa.String(500), nullable=False),
        sa.Column("peptide_length", sa.Integer(), nullable=False),
        sa.Column("hla_allele", sa.String(50), nullable=False),
        sa.Column("binding_affinity_nm", sa.Float(), nullable=False),
        sa.Column("presentation_score", sa.Float(), nullable=False),
        sa.Column("processing_score", sa.Float(), nullable=True),
        sa.Column("expression_tpm", sa.Float(), nullable=True),
        sa.Column("immunogenicity_score", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("explanation_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_epitopes_analysis_id", "epitopes", ["analysis_id"])
    op.create_index("ix_epitopes_variant_id", "epitopes", ["variant_id"])
    op.create_index("ix_epitopes_rank", "epitopes", ["rank"])
    op.create_index("ix_epitopes_immunogenicity_score", "epitopes", ["immunogenicity_score"])

    # -- job_logs --
    op.create_table(
        "job_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_job_logs_analysis_id", "job_logs", ["analysis_id"])


def downgrade() -> None:
    op.drop_table("job_logs")
    op.drop_table("epitopes")
    op.drop_table("variants")
    op.drop_table("hla_types")
    op.drop_table("analysis_inputs")
    op.drop_table("analyses")
    op.drop_table("projects")
    op.drop_table("users")
