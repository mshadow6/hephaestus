"""pending_approval and rejected statuses

Revision ID: 3eb43572e800
Revises: 71f791ab032f
Create Date: 2026-09-28 12:39:44.647058

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3eb43572e800'
down_revision: Union[str, None] = '71f791ab032f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alembic n'autodétecte pas les ajouts de valeurs sur un enum Postgres existant.
    op.execute("ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'pending_approval'")
    op.execute("ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'rejected'")


def downgrade() -> None:
    # Postgres ne permet pas de retirer une valeur d'enum sans recréer le type —
    # pas de downgrade automatique ici.
    pass
