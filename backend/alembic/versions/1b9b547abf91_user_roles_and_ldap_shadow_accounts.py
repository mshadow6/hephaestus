"""user roles and ldap shadow accounts

Revision ID: 1b9b547abf91
Revises: d1d93a744629
Create Date: 2026-09-28 13:43:55.476979

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b9b547abf91'
down_revision: Union[str, None] = 'd1d93a744629'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Le type enum Postgres doit exister avant l'ADD COLUMN qui l'utilise.
    op.execute("CREATE TYPE user_role AS ENUM ('admin', 'viewer')")

    # server_default nécessaire : la table a déjà des lignes (comptes créés avant cette
    # migration) — sans ça, l'ADD COLUMN NOT NULL échoue.
    op.add_column('users', sa.Column(
        'role', sa.Enum('admin', 'viewer', name='user_role'), nullable=False,
        server_default='admin',
    ))
    op.add_column('users', sa.Column(
        'source', sa.String(length=16), nullable=False, server_default='local',
    ))
    op.alter_column('users', 'password_hash',
               existing_type=sa.VARCHAR(length=255),
               nullable=True)


def downgrade() -> None:
    op.alter_column('users', 'password_hash',
               existing_type=sa.VARCHAR(length=255),
               nullable=False)
    op.drop_column('users', 'source')
    op.drop_column('users', 'role')
    op.execute("DROP TYPE user_role")
