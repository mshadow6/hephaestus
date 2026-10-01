"""add rejection_reason to vm_requests

Revision ID: a3c8f1d9e2b7
Revises: fee76afa014c
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa

revision = "a3c8f1d9e2b7"
down_revision = "fee76afa014c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vm_requests", sa.Column("rejection_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("vm_requests", "rejection_reason")
