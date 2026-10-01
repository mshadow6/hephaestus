"""add login lockout fields to users

Revision ID: 90b7a1f819f3
Revises: e5b2c9d1a4f7
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "90b7a1f819f3"
down_revision = "e5b2c9d1a4f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("is_protected", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Le tout premier admin créé à /setup est le compte "de secours" — ne peut jamais
    # être verrouillé définitivement par le mécanisme anti-bruteforce, pour ne jamais se
    # retrouver totalement enfermé hors de sa propre installation.
    op.execute(
        "UPDATE users SET is_protected = true WHERE id = (SELECT id FROM users ORDER BY id ASC LIMIT 1)"
    )


def downgrade() -> None:
    op.drop_column("users", "is_protected")
    op.drop_column("users", "disabled")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
