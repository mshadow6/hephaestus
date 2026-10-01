"""add approved_by and approved_at to vm_requests

Revision ID: e5b2c9d1a4f7
Revises: d4a1f6c8b9e2
Create Date: 2026-09-30
"""
from alembic import op
import sqlalchemy as sa

revision = "e5b2c9d1a4f7"
down_revision = "d4a1f6c8b9e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vm_requests", sa.Column("approved_by", sa.String(length=255), nullable=True))
    op.add_column("vm_requests", sa.Column("approved_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("vm_requests", "approved_at")
    op.drop_column("vm_requests", "approved_by")
