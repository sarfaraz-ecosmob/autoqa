"""phase 11: quality module columns + screenshot capture flag

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())

    def add_if_missing(table: str, column: str, column_def: sa.Column) -> None:
        # Idempotency guard: 0001 runs Base.metadata.create_all() from the LIVE
        # models, so on a fresh database these columns already exist — adding
        # them again fails with DuplicateColumn. Legacy databases (schema as of
        # this revision) still get the column added.
        if column not in {c["name"] for c in insp.get_columns(table)}:
            op.add_column(table, column_def)

    add_if_missing(
        "test_cases",
        "capture_on_pass",
        sa.Column("capture_on_pass", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    add_if_missing(
        "performance_results",
        "meta",
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    add_if_missing(
        "accessibility_results",
        "meta",
        sa.Column("meta", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("accessibility_results", "meta")
    op.drop_column("performance_results", "meta")
    op.drop_column("test_cases", "capture_on_pass")
