import logging
import secrets
from datetime import datetime

from app.config import settings
from app.connections.store import get_active_connection, get_connection, list_active_connections
from app.database import SessionLocal
from app.dns import get_dns_provider
from app.errors import describe_connector_error
from app.dns.base import DnsProvider
from app.ipam import get_ipam_provider
from app.models import PlaybookRun, PlaybookRunStatus, RequestStatus, VMRequest
from app.playbooks.store import get_playbook
from app.provisioning.ansible_runner import AnsibleError, run_adhoc_playbook_streaming, run_post_install
from app.provisioning.terraform_runner import ProvisioningError, apply_vm

logger = logging.getLogger(__name__)


class MissingConnectionError(RuntimeError):
    """Aucune connexion active pour une catégorie requise — voir écran Connexions."""


def _require_active_connection(category: str, label: str):
    conn = get_active_connection(category)
    if conn is None:
        raise MissingConnectionError(
            f"Aucune connexion {label} active — configure-en une (ou active-en une "
            f"existante) sur /connections avant de relancer cette requête."
        )
    return conn


def _resolve_hypervisor_connection(vm_request: VMRequest):
    """Plusieurs connexions "hypervisor" peuvent être actives à la fois (Proxmox +
    VMware, voir app.connections.store.MULTI_ACTIVE_CATEGORIES) - on utilise celle
    explicitement choisie dans le formulaire (`hypervisor_connection`, nom de la
    connexion) si elle existe et correspond toujours à une connexion active, sinon on
    retombe sur `get_active_connection` (compat demandes GLPI/anciennes sans ce choix -
    marche tant qu'une seule connexion hyperviseur est active)."""
    if vm_request.hypervisor_connection:
        conn = get_connection(vm_request.hypervisor_connection)
        if conn is not None and conn.enabled and conn.active:
            return conn
        raise MissingConnectionError(
            f"La connexion hyperviseur choisie ('{vm_request.hypervisor_connection}') "
            "n'existe plus ou n'est plus active - choisis-en une autre sur /connections."
        )
    conn = get_active_connection("hypervisor")
    if conn is None:
        raise MissingConnectionError(
            "Aucune connexion hyperviseur active - configure-en une (ou active-en une "
            "existante) sur /connections avant de relancer cette requête."
        )
    if len(list_active_connections("hypervisor")) > 1:
        logger.warning(
            "Requête %s sans hyperviseur choisi explicitement alors que plusieurs sont "
            "actifs - '%s' utilisée par défaut (premier trouvé), résultat possiblement "
            "pas celui attendu.", vm_request.id, conn.name,
        )
    return conn


def _record_step(
    db, vm_request: VMRequest, key: str, label: str, status: str, error: str | None = None
) -> None:
    """Ajoute ou met à jour une étape dans `vm_request.pipeline_steps` et commit
    immédiatement — affiché dans l'UI avec un badge par étape (voir _status_fragment.html).
    Réassigne toute la liste (pas de mutation en place) pour que SQLAlchemy détecte le
    changement sur la colonne JSON."""
    steps = list(vm_request.pipeline_steps or [])
    entry = {"key": key, "label": label, "status": status, "error": error}
    for i, s in enumerate(steps):
        if s.get("key") == key:
            steps[i] = entry
            break
    else:
        steps.append(entry)
    vm_request.pipeline_steps = steps
    db.commit()


def process_vm_request(request_id: int) -> None:
    """Point d'entrée du job RQ déclenché par le webhook.

    Étapes :
      1. Réservation IP (IPAM) + enregistrement DNS
      2. terraform apply (Proxmox), IP statique — jamais de DHCP pour un serveur
      3. Playbooks Ansible post-install (compte admin, durcissement SSH)

    Chaque connecteur (IPAM, DNS, hyperviseur, Gitea) vient de la connexion active
    correspondante (voir app.connections.store) — jamais lu en dur depuis .env.
    """
    db = SessionLocal()
    try:
        vm_request = db.get(VMRequest, request_id)
        if vm_request is None:
            logger.warning("VMRequest %s introuvable, job ignoré", request_id)
            return

        try:
            ipam_conn = _require_active_connection("ipam", "IPAM")
            hypervisor_conn = _resolve_hypervisor_connection(vm_request)
            gitea_conn = _require_active_connection("scm", "Gitea (playbooks Ansible)")
        except MissingConnectionError as exc:
            logger.error("Requête %s bloquée: %s", vm_request.id, exc)
            vm_request.status = RequestStatus.failed
            vm_request.error_message = str(exc)
            db.commit()
            return

        try:
            ipam = get_ipam_provider(ipam_conn.type, ipam_conn.config)
            reservation = ipam.reserve_ip(vlan=vm_request.vlan or "default", hostname=vm_request.hostname)
            logger.info(
                "IP réservée pour la requête %s (%s) via %s (%s): %s",
                vm_request.id,
                vm_request.hostname,
                ipam_conn.name,
                ipam_conn.type,
                reservation,
            )

            # DNS : soit l'IPAM est tout-en-un et implémente déjà DnsProvider (ex: EfficientIP),
            # soit un connecteur DNS séparé est configuré (ex: phpIPAM + Windows DNS), soit rien.
            dns_conn = get_active_connection("dns")
            if isinstance(ipam, DnsProvider):
                dns = ipam
            elif dns_conn is not None:
                dns = get_dns_provider(dns_conn.type, dns_conn.config)
            else:
                dns = None

            if dns is not None:
                dns.create_record(hostname=vm_request.hostname, ip=reservation.ip)
                logger.info(
                    "Enregistrement DNS créé pour la requête %s (%s -> %s)",
                    vm_request.id, vm_request.hostname, reservation.ip,
                )
            else:
                logger.info(
                    "Pas de connecteur DNS configuré — enregistrement DNS ignoré pour la requête %s",
                    vm_request.id,
                )
        except Exception as exc:  # noqa: BLE001 — n'importe quelle panne IPAM/DNS (hôte
            # injoignable, timeout, erreur API...) doit faire échouer proprement la
            # requête plutôt que planter le job RQ et laisser le statut bloqué
            # silencieusement sur "pending" pour toujours (trouvé en conditions réelles :
            # phpIPAM injoignable depuis un réseau distant sans route vers le LAN
            # homelab — timeout de connexion, jamais rattrapé avant ce correctif).
            logger.error(
                "Réservation IP/DNS échouée pour la requête %s (%s) : %s",
                vm_request.id, vm_request.hostname, exc,
            )
            vm_request.status = RequestStatus.failed
            vm_request.error_message = f"Réservation IP/DNS échouée : {describe_connector_error(exc)}"
            db.commit()
            return

        vm_request.ip_address = reservation.ip
        vm_request.netmask = reservation.netmask
        vm_request.gateway = reservation.gateway
        vm_request.status = RequestStatus.ip_reserved
        db.commit()

        vm_request.status = RequestStatus.provisioning
        db.commit()

        # Mot de passe root éphémère, unique à cette requête — sert une seule fois au
        # bootstrap Ansible (deploy-ssh-keys.yml), jamais stocké au-delà de ce job.
        bootstrap_root_password = secrets.token_urlsafe(24)

        _record_step(db, vm_request, "vm_create", "Création VM", "running")

        try:
            result = apply_vm(vm_request, reservation, bootstrap_root_password, hypervisor_conn)
        except ProvisioningError as exc:
            logger.error(
                "terraform apply échoué pour la requête %s (%s): %s\nstderr:\n%s",
                vm_request.id, vm_request.hostname, exc, exc.stderr,
            )
            vm_request.status = RequestStatus.vm_failed
            vm_request.error_message = str(exc) + ("\n\n" + exc.stderr[-4000:] if exc.stderr else "")
            _record_step(db, vm_request, "vm_create", "Création VM", "failed", vm_request.error_message)
            return
        except Exception as exc:  # noqa: BLE001 — un bug imprévu ici (ex: TypeError sur
            # un champ mal géré) plantait tout le job RQ sans jamais repasser par le except
            # ProvisioningError ni mettre à jour le statut : la demande restait bloquée sur
            # "provisioning" pour toujours, sans message d'erreur visible dans l'UI (trouvé
            # en conditions réelles : vlan_tag=None -> int(None) non rattrapé).
            logger.exception(
                "Erreur inattendue pendant terraform apply pour la requête %s (%s)",
                vm_request.id, vm_request.hostname,
            )
            vm_request.status = RequestStatus.vm_failed
            vm_request.error_message = f"Erreur inattendue pendant la création de la VM : {exc}"
            _record_step(db, vm_request, "vm_create", "Création VM", "failed", vm_request.error_message)
            return

        vm_request.status = RequestStatus.vm_created
        vm_request.hypervisor_vm_id = str(result.vm_id)
        if hypervisor_conn.type == "proxmox":
            vm_request.proxmox_vmid = int(result.vm_id)  # compat affichage historique
        db.commit()
        _record_step(db, vm_request, "vm_create", "Création VM", "success")
        logger.info(
            "VM Proxmox créée pour la requête %s (%s): vmid=%s",
            vm_request.id, vm_request.hostname, result.vm_id,
        )

        vm_request.status = RequestStatus.post_install
        db.commit()

        selected_playbooks = [
            pb for pb in (get_playbook(key) for key in (vm_request.selected_playbooks or []))
            if pb is not None
        ]

        def _on_step(key: str, label: str, status: str, error: str | None = None) -> None:
            _record_step(db, vm_request, key, label, status, error)

        try:
            run_post_install(
                vm_request, bootstrap_root_password, gitea_conn.config, selected_playbooks,
                on_step=_on_step,
            )
        except AnsibleError as exc:
            logger.error(
                "Post-install Ansible échoué pour la requête %s (%s): %s\nstderr:\n%s",
                vm_request.id, vm_request.hostname, exc, exc.stderr,
            )
            # La VM existe et reste joignable (via le mot de passe root éphémère si
            # deploy-ssh-keys.yml n'a pas fini, ou via le compte admin sinon) — statut
            # dédié (pas vm_failed ni failed générique) pour distinguer "VM absente" de
            # "VM créée et utilisable, juste un playbook post-install à reprendre".
            vm_request.status = RequestStatus.post_install_failed
            # ansible-playbook écrit le détail réel d'une tâche en échec (fatal: [...] =>
            # {...}) sur stdout, pas stderr (qui ne contient souvent qu'un warning Python
            # sans rapport) — ne garder que stderr masquait la vraie raison de l'échec
            # dans l'UI (trouvé en conditions réelles : update-os.yml, seul un warning
            # d'interpréteur Python remontait, la tâche fatale réelle restait invisible).
            details = "\n\n".join(
                part for part in (
                    ("stdout:\n" + exc.stdout[-4000:]) if exc.stdout else "",
                    ("stderr:\n" + exc.stderr[-2000:]) if exc.stderr else "",
                ) if part
            )
            vm_request.error_message = str(exc) + ("\n\n" + details if details else "")
            db.commit()
            return

        vm_request.status = RequestStatus.done
        db.commit()
        logger.info(
            "Post-install terminé pour la requête %s (%s) — root fermé en SSH, compte %s opérationnel",
            vm_request.id, vm_request.hostname, settings.ansible_admin_username,
        )
    finally:
        db.close()


def run_adhoc_playbook_job(run_id: int) -> None:
    """Exécute un playbook du catalogue à la demande sur des VMs déjà provisionnées,
    déclenché depuis /playbooks/deploy — pas lié à une création de VM."""
    db = SessionLocal()
    try:
        run = db.get(PlaybookRun, run_id)
        if run is None:
            logger.warning("PlaybookRun %s introuvable, job ignoré", run_id)
            return

        gitea_conn = get_active_connection("scm")
        if gitea_conn is None:
            run.status = PlaybookRunStatus.failed
            run.output = "Aucune connexion Gitea (scm) active — voir /connections."
            run.finished_at = datetime.utcnow()
            db.commit()
            return

        playbook = get_playbook(run.playbook_key)
        if playbook is None:
            run.status = PlaybookRunStatus.failed
            run.output = f"Playbook '{run.playbook_key}' introuvable au catalogue."
            run.finished_at = datetime.utcnow()
            db.commit()
            return

        run.status = PlaybookRunStatus.running
        run.output = ""
        db.commit()

        # target_hostnames = {"group": <groupe de l'inventaire persistant> | None,
        # "manual": [<IP/nom DNS>, ...]} — voir app.routers.playbooks.deploy et
        # app.provisioning.inventory_file. Le suivi est en direct (on_output), pas
        # attendu en bloc à la fin — commit périodique pour que le polling côté
        # dashboard affiche la progression pendant l'exécution.
        buffer: list[str] = []
        last_commit = datetime.utcnow()

        def on_output(line: str) -> None:
            nonlocal last_commit
            buffer.append(line)
            now = datetime.utcnow()
            if (now - last_commit).total_seconds() >= 2 or len(buffer) >= 30:
                run.output = (run.output or "") + "".join(buffer)
                buffer.clear()
                db.commit()
                last_commit = now

        targets = run.target_hostnames or {}
        try:
            run_adhoc_playbook_streaming(
                playbook.repo_path, targets.get("group"), targets.get("manual") or [],
                gitea_conn.config, on_output,
                dry_run=run.dry_run, verbose=run.verbose,
            )
            run.output = (run.output or "") + "".join(buffer)
            run.status = PlaybookRunStatus.success
        except AnsibleError as exc:
            run.output = (run.output or "") + "".join(buffer) + f"\n\n{exc}"
            run.status = PlaybookRunStatus.failed
            logger.error("PlaybookRun %s (%s) échoué : %s", run.id, run.playbook_key, exc)

        run.finished_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
