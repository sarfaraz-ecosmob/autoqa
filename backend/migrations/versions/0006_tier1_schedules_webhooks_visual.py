"""tier-1: schedules, webhooks, visual testing baselines

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("cron", sa.String(100), nullable=False),
        sa.Column("browsers", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("environment_id", sa.String(36), sa.ForeignKey("test_environments.id"), nullable=True),
        sa.Column("suite", sa.String(200), nullable=True),
        sa.Column("refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parallelism", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_id", sa.String(36), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "webhooks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url_encrypted", sa.Text(), nullable=False, server_default=""),
        sa.Column("events", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("secret", sa.String(100), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_status", sa.String(30), nullable=False, server_default=""),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "visual_baselines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("test_case_id", sa.String(36), sa.ForeignKey("test_cases.id"), nullable=False),
        sa.Column("browser", sa.String(20), nullable=False, server_default="chromium"),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("test_case_id", "browser", name="uq_visual_baseline_case_browser"),
    )

    op.create_table(
        "visual_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("test_case_id", sa.String(36), sa.ForeignKey("test_cases.id"), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False, index=True),
        sa.Column("browser", sa.String(20), nullable=False, server_default="chromium"),
        sa.Column("baseline_key", sa.String(500), nullable=False, server_default=""),
        sa.Column("current_key", sa.String(500), nullable=False, server_default=""),
        sa.Column("diff_key", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("diff_percent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("visual_checks")
    op.drop_table("visual_baselines")
    op.drop_table("webhooks")
    op.drop_table("test_schedules")
