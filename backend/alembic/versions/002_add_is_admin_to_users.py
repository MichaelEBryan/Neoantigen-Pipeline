"""Add is_admin column to users table.

Revision ID: 002
Revises: 001
Create Date: 2026-04-03

Seeds michael.bryan@new.ox.ac.uk as the default admin.
"""
from alembic import op
import sqlalchemy as sa


revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Seed default admin
    op.execute(
        "UPDATE users SET is_admin = true WHERE LOWER(email) = 'michael.bryan@new.ox.ac.uk'"
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
