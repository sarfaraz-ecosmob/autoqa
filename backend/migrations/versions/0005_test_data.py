"""phase 14: test environments & datasets (§17)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_environments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), index=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("variables", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "test_datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), index=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="static"),
        sa.Column("generator", sa.String(50), nullable=False, server_default=""),
        sa.Column("generator_params", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("values", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("encrypted_keys", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column(
            "environment_id",
            sa.String(36),
            sa.ForeignKey("test_environments.id"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("test_datasets")
    op.drop_table("test_environments")
