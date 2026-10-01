"""add selected_playbooks to vm_requests

Revision ID: b7e4a2c910d3
Revises: a3c8f1d9e2b7
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa

revision = "b7e4a2c910d3"
down_revision = "a3c8f1d9e2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vm_requests", sa.Column("selected_playbooks", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("vm_requests", "selected_playbooks")
