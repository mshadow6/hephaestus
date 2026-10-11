"""add pipeline_steps to vm_requests

Revision ID: f2a9c4e7b1d6
Revises: 90b7a1f819f3
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "f2a9c4e7b1d6"
down_revision = "90b7a1f819f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vm_requests", sa.Column("pipeline_steps", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("vm_requests", "pipeline_steps")
