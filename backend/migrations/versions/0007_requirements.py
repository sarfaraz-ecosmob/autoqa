"""requirements and traceability

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-17
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Mirror of the Priority enum values in app/models.py. The PG type `priority`
# was created by migration 0001 — reference it, never re-create it.
_PRIORITY_VALUES = ("critical", "high", "medium", "low")


def upgrade() -> None:
    # postgresql.ENUM with create_type=False is required: a plain sa.Enum would
    # emit CREATE TYPE inside op.create_table and fail (type exists since 0001).
    priority_enum = postgresql.ENUM(*_PRIORITY_VALUES, name="priority", create_type=False)
    op.create_table(
        "requirements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("external_id", sa.String(60), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(200), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", priority_enum, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_req_project_external", "requirements", ["project_id", "external_id"], unique=True
    )

    op.create_table(
        "requirement_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False, index=True),
        sa.Column("requirement_id", sa.String(36), sa.ForeignKey("requirements.id"), nullable=False, index=True),
        sa.Column("test_case_id", sa.String(36), sa.ForeignKey("test_cases.id"), nullable=False, index=True),
        sa.Column("coverage", sa.String(20), nullable=False, server_default="ui"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_req_link", "requirement_links", ["requirement_id", "test_case_id"])


def downgrade() -> None:
    op.drop_table("requirement_links")
    op.drop_table("requirements")
    # The shared `priority` enum already existed; nothing to drop.
