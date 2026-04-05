"""Add user_preferences table for settings, analysis defaults, scoring weights, theme.

Revision ID: 005
Revises: 004
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), unique=True, nullable=False),

        # Analysis defaults
        sa.Column("default_cancer_type", sa.String(255), nullable=True),
        sa.Column("default_stage", sa.String(10), nullable=True),
        sa.Column("default_genome", sa.String(50), nullable=True),
        sa.Column("default_hla_alleles", sa.Text(), nullable=True),

        # Scoring weights
        sa.Column("weight_presentation", sa.Float(), nullable=True),
        sa.Column("weight_binding_rank", sa.Float(), nullable=True),
        sa.Column("weight_expression", sa.Float(), nullable=True),
        sa.Column("weight_vaf", sa.Float(), nullable=True),
        sa.Column("weight_mutation_type", sa.Float(), nullable=True),
        sa.Column("weight_processing", sa.Float(), nullable=True),
        sa.Column("weight_iedb", sa.Float(), nullable=True),

        # Display preferences
        sa.Column("theme", sa.String(20), nullable=True),
        sa.Column("results_page_size", sa.Integer(), nullable=True),
        sa.Column("default_visible_columns", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
