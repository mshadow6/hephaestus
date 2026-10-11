"""add groups table, migrate users.role to users.group_id

Revision ID: b1e4d7a2c9f3
Revises: a7c3e9f1b4d8
Create Date: 2026-10-06
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "b1e4d7a2c9f3"
down_revision = "a7c3e9f1b4d8"
branch_labels = None
depends_on = None

# Doit rester synchro avec app.permissions.ALL_PERMISSIONS au moment de cette migration —
# figé ici volontairement (une migration ne doit pas dépendre d'un import applicatif qui
# pourrait changer plus tard et casser un replay de l'historique des migrations).
ALL_PERMISSIONS = [
    "approve_vm", "manage_connectors", "manage_playbooks", "run_playbooks",
    "manage_settings", "manage_users", "manage_groups",
]


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("permissions", sa.JSON, nullable=False),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    conn = op.get_bind()
    admin_id = conn.execute(
        sa.text("INSERT INTO groups (name, permissions, is_system) VALUES ('Admin', :p, true) RETURNING id"),
        {"p": json.dumps(ALL_PERMISSIONS)},
    ).scalar()
    viewer_id = conn.execute(
        sa.text("INSERT INTO groups (name, permissions, is_system) VALUES ('Invité', :p, true) RETURNING id"),
        {"p": json.dumps([])},
    ).scalar()

    op.add_column("users", sa.Column("group_id", sa.Integer, sa.ForeignKey("groups.id"), nullable=True))
    conn.execute(sa.text("UPDATE users SET group_id = :g WHERE role = 'admin'"), {"g": admin_id})
    conn.execute(sa.text("UPDATE users SET group_id = :g WHERE role != 'admin' OR role IS NULL"), {"g": viewer_id})
    op.alter_column("users", "group_id", nullable=False)

    op.drop_column("users", "role")
    with op.get_context().autocommit_block():
        op.execute("DROP TYPE IF EXISTS user_role")


def downgrade() -> None:
    # Pas de downgrade propre : les permissions custom créées entre-temps n'ont pas
    # d'équivalent dans l'ancien modèle à 2 rôles fixes — jamais fait sur ce projet.
    pass
