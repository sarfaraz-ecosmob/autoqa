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
    # Idempotency guard: 0001 (create_all from the live models) already created
    # this column on a fresh database.
    insp = sa.inspect(op.get_bind())
    if "test_types" not in {c["name"] for c in insp.get_columns("test_suites")}:
        op.add_column(
            "test_suites",
            sa.Column("test_types", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    op.drop_column("test_suites", "test_types")
