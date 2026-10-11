"""add post_install_failed status to request_status enum

Revision ID: a7c3e9f1b4d8
Revises: f2a9c4e7b1d6
Create Date: 2026-10-05
"""
from alembic import op

revision = "a7c3e9f1b4d8"
down_revision = "f2a9c4e7b1d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE ne peut pas tourner dans une transaction explicite côté
    # Postgres (avant PG12 en tout cas, et Alembic ouvre une transaction par défaut) —
    # autocommit_block() la sort de la transaction de migration.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'post_install_failed'")


def downgrade() -> None:
    # Postgres ne supporte pas DROP VALUE sur un type enum — pas de downgrade propre
    # possible sans recréer le type entier (hors scope ici, jamais fait sur ce projet).
    pass
