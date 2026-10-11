"""add hypervisor_connection and hypervisor_vm_id to vm_requests

Revision ID: c3f8a1d2b7e5
Revises: b1e4d7a2c9f3
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "c3f8a1d2b7e5"
down_revision = "b1e4d7a2c9f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vm_requests", sa.Column("hypervisor_connection", sa.String(64), nullable=True))
    op.add_column("vm_requests", sa.Column("hypervisor_vm_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("vm_requests", "hypervisor_vm_id")
    op.drop_column("vm_requests", "hypervisor_connection")
