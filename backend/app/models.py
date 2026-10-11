import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    # Distinct de `failed` : la VM existe bel et bien et reste utilisable (clé SSH admin
    # déployée), seul un playbook post-install (ex: update-os.yml) a échoué — pas la même
    # situation que "rien n'a jamais été créé" (failed) ou "Terraform a planté" (vm_failed).
    post_install_failed = "post_install_failed"


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
    # Nom de la connexion "hypervisor" ciblée (plusieurs peuvent être actives en même
    # temps - Proxmox et VMware par ex., voir app.connections.store.MULTI_ACTIVE_CATEGORIES)
    # - choisi explicitement dans le formulaire, jamais déduit implicitement.
    hypervisor_connection: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Identifiant de la VM côté hyperviseur, générique (UUID vSphere ou "123" Proxmox) -
    # proxmox_vmid (Integer) reste pour compat/affichage historique côté Proxmox.
    hypervisor_vm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

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

    # Suivi détaillé étape par étape (création VM, puis chaque playbook), affiché dans
    # l'UI avec un badge vert/rouge par étape — sans ça un échec sur un playbook tardif
    # (ex: update-os.yml) donnait l'impression que "la VM n'était pas déployée" alors
    # qu'elle l'était bel et bien, seule une étape ultérieure avait échoué. Liste de
    # `{"key", "label", "status": "queued"|"running"|"success"|"failed", "error"}`,
    # dans l'ordre d'exécution — voir app.worker.tasks._record_step.
    pipeline_steps: Mapped[list | None] = mapped_column(JSON, nullable=True)

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


class Group(Base):
    """Groupe de permissions, librement créable (voir app.permissions.ALL_PERMISSIONS
    pour le vocabulaire fixe des permissions attribuables). `Admin` et `Invité` sont les
    deux groupes système créés par la migration initiale (ni supprimables ni
    renommables, `is_system=True`) — garantit qu'il existe toujours un groupe avec tous
    les droits et un groupe le plus restrictif possible, même si l'app est mal
    configurée par ailleurs."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    """Compte du dashboard, local ou LDAP (voir app.auth.ldap_client). password_hash est
    vide pour un compte LDAP — l'authentification passe par un bind sur l'annuaire."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    group: Mapped["Group"] = relationship(lazy="joined")
    source: Mapped[str] = mapped_column(String(16), default="local")  # "local" | "ldap" | "keycloak"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Anti-bruteforce : 5 échecs -> verrouillage 1 min (auto-expire via locked_until) ;
    # 5 échecs de plus -> disabled=True (verrouillage définitif, un admin doit réactiver
    # depuis /settings/users). S'applique aussi aux comptes LDAP (le bind échoué compte
    # pareil) — la source de l'échec ne change rien au risque de bruteforce côté app.
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disabled: Mapped[bool] = mapped_column(default=False)
    # Compte de secours (le tout premier admin créé à /setup) : jamais verrouillable
    # définitivement par le mécanisme ci-dessus, pour ne jamais perdre tout accès à sa
    # propre installation. Reste soumis au verrouillage temporaire de 1 min.
    is_protected: Mapped[bool] = mapped_column(default=False)
