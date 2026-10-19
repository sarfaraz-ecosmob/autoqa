"""suite test plan binding

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotency guards: 0001 (create_all from the live models) already created
    # the column and an equivalent FK on a fresh database (PG auto-names it
    # test_suites_test_plan_id_fkey), so only add what is actually missing.
    insp = sa.inspect(op.get_bind())

    if "test_plan_id" not in {c["name"] for c in insp.get_columns("test_suites")}:
        op.add_column("test_suites", sa.Column("test_plan_id", sa.String(36), nullable=True))

    has_plan_fk = any(
        "test_plan_id" in (fk.get("constrained_columns") or [])
        for fk in insp.get_foreign_keys("test_suites")
    )
    if not has_plan_fk:
        # ON DELETE SET NULL: deleting a plan version clears the group's plan pin
        # instead of blocking (groups must survive plan regeneration/cleanup).
        op.create_foreign_key(
            "fk_test_suites_test_plan",
            "test_suites",
            "test_plans",
            ["test_plan_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if "ix_test_suites_test_plan_id" not in {ix["name"] for ix in insp.get_indexes("test_suites")}:
        op.create_index("ix_test_suites_test_plan_id", "test_suites", ["test_plan_id"])


def downgrade() -> None:
    op.drop_index("ix_test_suites_test_plan_id", table_name="test_suites")
    op.drop_constraint("fk_test_suites_test_plan", "test_suites", type_="foreignkey")
    op.drop_column("test_suites", "test_plan_id")
