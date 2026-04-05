"""Add DAI (Differential Agretopicity Index) columns to epitopes.

Revision ID: 004
Revises: 003
Create Date: 2026-04-04

Adds dai_score and wt_binding_affinity_nm to the epitopes table.
DAI compares wildtype vs mutant MHC binding: positive DAI means
the mutant peptide binds MHC better than WT, making it a stronger
neoantigen candidate.
"""
from alembic import op
import sqlalchemy as sa


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "epitopes",
        sa.Column("dai_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "epitopes",
        sa.Column("wt_binding_affinity_nm", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("epitopes", "wt_binding_affinity_nm")
    op.drop_column("epitopes", "dai_score")
