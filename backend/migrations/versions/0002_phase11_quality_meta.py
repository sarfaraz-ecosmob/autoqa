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
    op.add_column(
        "test_cases",
        sa.Column("capture_on_pass", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "performance_results",
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "accessibility_results",
        sa.Column("meta", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("accessibility_results", "meta")
    op.drop_column("performance_results", "meta")
    op.drop_column("test_cases", "capture_on_pass")
