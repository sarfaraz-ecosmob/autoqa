"""suite test types

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # [] = no type restriction (group runs every category it contains).
    op.add_column(
        "test_suites",
        sa.Column("test_types", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("test_suites", "test_types")
