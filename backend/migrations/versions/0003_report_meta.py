"""phase 12: reports.meta column

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotency guard: on a fresh database 0001 (create_all from the live
    # models) already created this column.
    insp = sa.inspect(op.get_bind())
    if "meta" not in {c["name"] for c in insp.get_columns("reports")}:
        op.add_column(
            "reports",
            sa.Column("meta", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        )


def downgrade() -> None:
    op.drop_column("reports", "meta")
