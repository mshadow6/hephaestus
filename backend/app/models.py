import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RequestStatus(str, enum.Enum):
    pending_approval = "pending_approval"
    rejected = "rejected"
    pending = "pending"
    ip_reserved = "ip_reserved"
    provisioning = "provisioning"
    vm_created = "vm_created"
    vm_failed = "vm_failed"
    post_install = "post_install"
    done = "done"
    failed = "failed"


class VMRequest(Base):
    __tablename__ = "vm_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Référence côté GLPI/Formcreator
    glpi_ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Caractéristiques de la VM demandée
    hostname: Mapped[str] = mapped_column(String(255))
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vlan: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ram_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    os_template: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Renseigné par le worker après réservation IPAM — IP statique, jamais de DHCP pour
    # un serveur (netmask = longueur de préfixe CIDR, ex: "24")
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    netmask: Mapped[str | None] = mapped_column(String(8), nullable=True)
    gateway: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Renseigné par le worker après terraform apply
    proxmox_vmid: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Charge utile brute du webhook, conservée pour audit/rejeu
    raw_payload: Mapped[dict] = mapped_column(JSON)

    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, name="request_status"), default=RequestStatus.pending
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Argumentaire humain saisi par l'admin au clic sur "Rejeter" — distinct
    # d'error_message (qui documente un échec technique du pipeline, pas une décision).
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Traçabilité : qui a approuvé cette création, et quand — distinct de requested_by
    # (qui vient du ticket GLPI, pas forcément la même personne que celle qui valide).
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Clés du catalogue app.playbooks.store choisies à l'approbation (playbooks
    # "optionnel" cochés — les "obligatoire" sont toujours ajoutés côté serveur, pas
    # besoin de les lister ici). Le durcissement SSH n'est pas dans cette liste : il
    # tourne toujours, indépendamment du catalogue.
    selected_playbooks: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class PlaybookRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"


class PlaybookRun(Base):
    """Exécution à la demande d'un playbook du catalogue contre un ou plusieurs
    serveurs existants (pas liée à une création de VM — voir /playbooks/deploy),
    contrairement à VMRequest.selected_playbooks qui ne tourne qu'à la création."""

    __tablename__ = "playbook_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playbook_key: Mapped[str] = mapped_column(String(64))
    playbook_label: Mapped[str] = mapped_column(String(255))
    # {"group": "<groupe de l'inventaire persistant>" | None, "manual": [<IP/nom DNS>, ...]}
    target_hostnames: Mapped[dict] = mapped_column(JSON)
    status: Mapped[PlaybookRunStatus] = mapped_column(
        Enum(PlaybookRunStatus, name="playbook_run_status"), default=PlaybookRunStatus.queued
    )
    launched_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dry_run: Mapped[bool] = mapped_column(default=False)
    verbose: Mapped[bool] = mapped_column(default=False)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserRole(str, enum.Enum):
    admin = "admin"
    viewer = "viewer"


class User(Base):
    """Compte du dashboard, local ou LDAP (voir app.auth.ldap_client). password_hash est
    vide pour un compte LDAP — l'authentification passe par un bind sur l'annuaire."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), default=UserRole.admin)
    source: Mapped[str] = mapped_column(String(16), default="local")  # "local" | "ldap"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
