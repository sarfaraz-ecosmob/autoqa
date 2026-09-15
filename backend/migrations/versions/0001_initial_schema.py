"""initial schema - all spec §23 entities

Revision ID: 0001
Revises:
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

from app.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create every table from the declarative metadata (single source of truth)
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
