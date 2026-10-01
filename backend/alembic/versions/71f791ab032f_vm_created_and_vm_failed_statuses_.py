"""vm_created and vm_failed statuses, proxmox_vmid

Revision ID: 71f791ab032f
Revises: 9ee4541e3057
Create Date: 2026-09-18 23:26:46.553670

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71f791ab032f'
down_revision: Union[str, None] = '9ee4541e3057'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alembic n'autodétecte pas les ajouts de valeurs sur un enum Postgres existant.
    op.execute("ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'vm_created'")
    op.execute("ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'vm_failed'")

    op.add_column('vm_requests', sa.Column('proxmox_vmid', sa.Integer(), nullable=True))


def downgrade() -> None:
    # Postgres ne permet pas de retirer une valeur d'enum sans recréer le type
    # (et migrer toute donnée qui l'utilise) — pas de downgrade automatique ici.
    op.drop_column('vm_requests', 'proxmox_vmid')
