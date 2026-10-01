"""add playbook_runs table

Revision ID: c1f5b8a3e6d4
Revises: b7e4a2c910d3
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa

revision = "c1f5b8a3e6d4"
down_revision = "b7e4a2c910d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playbook_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("playbook_key", sa.String(length=64), nullable=False),
        sa.Column("playbook_label", sa.String(length=255), nullable=False),
        sa.Column("target_hostnames", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "running", "success", "failed", name="playbook_run_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("launched_by", sa.String(length=255), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("playbook_runs")
    sa.Enum(name="playbook_run_status").drop(op.get_bind(), checkfirst=True)
