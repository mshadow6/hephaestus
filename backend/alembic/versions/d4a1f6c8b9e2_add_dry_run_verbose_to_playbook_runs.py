"""add dry_run and verbose to playbook_runs

Revision ID: d4a1f6c8b9e2
Revises: c1f5b8a3e6d4
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa

revision = "d4a1f6c8b9e2"
down_revision = "c1f5b8a3e6d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("playbook_runs", sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("playbook_runs", sa.Column("verbose", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("playbook_runs", "verbose")
    op.drop_column("playbook_runs", "dry_run")
