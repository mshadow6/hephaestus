import crypt
import json
import logging
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from app.config import settings
from app.models import VMRequest
from app.provisioning.inventory_file import INVENTORY_PATH

logger = logging.getLogger(__name__)

PLAYBOOKS_DIR = Path("/app/ansible-playbooks")

# Correspond exactement à vm-hardening/deploy-ssh-keys.yml puis harden-root.yml dans le
# repo Gitea homelab-playbooks (voir README de ce repo pour le détail de la démarche).


class AnsibleError(RuntimeError):
    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(message)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300, env: dict | None = None) -> subprocess.CompletedProcess:
    logger.info("Exécution: %s (cwd=%s)", " ".join(cmd), cwd)
    full_env = os.environ.copy()
    full_env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    if env:
        full_env.update(env)
    return subprocess.run(cmd, cwd=cwd, env=full_env, capture_output=True, text=True, timeout=timeout)


def _run_streaming(cmd: list[str], cwd: Path, env: dict, on_output, timeout: int = 1800) -> None:
    """Comme _run, mais appelle on_output(line) au fil de l'eau (stdout+stderr
    fusionnés) au lieu d'attendre la fin du process — pour un suivi live côté UI.
    Lève AnsibleError si le process sort en erreur, avec la sortie complète accumulée."""
    logger.info("Exécution (streaming): %s (cwd=%s)", " ".join(cmd), cwd)
    full_env = os.environ.copy()
    full_env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    full_env["PYTHONUNBUFFERED"] = "1"
    full_env["ANSIBLE_FORCE_COLOR"] = "0"
    full_env.update(env)

    process = subprocess.Popen(
        cmd, cwd=cwd, env=full_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    collected: list[str] = []
    try:
        for line in process.stdout:  # type: ignore[union-attr]
            collected.append(line)
            on_output(line)
        process.wait(timeout=timeout)
    finally:
        if process.poll() is None:
            process.kill()

    full_output = "".join(collected)
    if process.returncode != 0:
        raise AnsibleError(f"{' '.join(cmd[-1:])} a échoué (code {process.returncode})", full_output, "")


def wait_for_ssh(host: str, timeout: int = 120, interval: int = 5) -> None:
    """Attend que le port 22 réponde sur la VM fraîchement créée avant de lancer Ansible."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, 22), timeout=3):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(interval)
    raise AnsibleError(f"SSH indisponible sur {host} après {timeout}s : {last_error}")


def sync_playbooks_repo(gitea_config: dict) -> Path:
    """Clone ou met à jour (git pull) le repo Gitea homelab-playbooks, et installe ses
    dépendances de collections Ansible (requirements.yml).

    `gitea_config` vient de la connexion "scm" (type gitea) active. Clone en **SSH avec
    une deploy key dédiée, lecture seule** (`settings.gitea_ssh_private_key_path`) —
    jamais de token en clair dans l'URL de clone (visible en clair dans `ps aux` sur
    l'hôte sinon). Volontairement une clé différente de celle utilisée pour se connecter
    aux VMs cibles (`ansible_ssh_private_key_path`) : une clé pour récupérer le contenu,
    une autre pour déployer dessus, jamais la même.
    """
    ssh_url = gitea_config.get("ssh_url", "")
    if not ssh_url:
        raise AnsibleError("ssh_url manquant sur la connexion Gitea")

    git_env = {
        "GIT_SSH_COMMAND": (
            f"ssh -i {settings.gitea_ssh_private_key_path} "
            "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        ),
    }

    if (PLAYBOOKS_DIR / ".git").exists():
        result = _run(["git", "pull", "--ff-only"], cwd=PLAYBOOKS_DIR, timeout=60, env=git_env)
    else:
        PLAYBOOKS_DIR.mkdir(parents=True, exist_ok=True)
        result = _run(["git", "clone", ssh_url, str(PLAYBOOKS_DIR)], timeout=60, env=git_env)
    if result.returncode != 0:
        raise AnsibleError(
            "git clone/pull du repo homelab-playbooks a échoué", result.stdout, result.stderr
        )

    collections_path = PLAYBOOKS_DIR / ".ansible-collections"
    req_file = PLAYBOOKS_DIR / "requirements.yml"
    if req_file.exists():
        gal = _run(
            ["ansible-galaxy", "collection", "install", "-r", "requirements.yml", "-p", str(collections_path)],
            cwd=PLAYBOOKS_DIR, timeout=120,
        )
        if gal.returncode != 0:
            raise AnsibleError(
                "ansible-galaxy collection install a échoué", gal.stdout, gal.stderr
            )
    return PLAYBOOKS_DIR


def _automation_public_key() -> str:
    pub_path = Path(settings.ansible_ssh_private_key_path + ".pub")
    if not pub_path.exists():
        raise AnsibleError(f"Clé publique d'automatisation introuvable : {pub_path}")
    return pub_path.read_text().strip()


def _admin_password_hash() -> str:
    if not settings.ansible_admin_password:
        raise AnsibleError(
            "ansible_admin_password non configuré (.env) — sudo serait inutilisable sur le compte admin"
        )
    return crypt.crypt(settings.ansible_admin_password, crypt.mksalt(crypt.METHOD_SHA512))


def _write_inventory(path: Path, group: str, ip: str, host_vars: dict[str, str]) -> None:
    vars_str = " ".join(f"{k}={v}" for k, v in host_vars.items())
    path.write_text(f"[{group}]\n{ip} {vars_str}\n")


def _write_inventory_multi(path: Path, group: str, ips: list[str], host_vars: dict[str, str]) -> None:
    vars_str = " ".join(f"{k}={v}" for k, v in host_vars.items())
    lines = [f"[{group}]"] + [f"{ip} {vars_str}" for ip in ips]
    path.write_text("\n".join(lines) + "\n")


_SSH_COMMON_ARGS = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"


def run_post_install(
    vm_request: VMRequest, bootstrap_root_password: str, gitea_config: dict,
    selected_playbooks: list | None = None,
) -> None:
    """Déploie le compte admin + clés SSH (deploy-ssh-keys.yml) puis ferme root en SSH
    (harden-root.yml) sur la VM tout juste créée. Lève AnsibleError si une étape échoue —
    l'appelant décide quoi faire (statut vm_created conservé, la VM reste utilisable
    manuellement même si le post-install a échoué).

    `gitea_config` vient de la connexion "scm" (type gitea) active.

    `selected_playbooks` : liste de `app.playbooks.store.PlaybookDef` (obligatoires +
    optionnels cochés à l'approbation) à exécuter après le durcissement SSH, dans
    l'ordre. Un playbook dont le fichier n'existe pas encore dans le repo est ignoré
    (log warning), pas une erreur — le catalogue peut lister des playbooks pas encore
    écrits.
    """
    repo_dir = sync_playbooks_repo(gitea_config)
    ip = vm_request.ip_address
    if not ip:
        raise AnsibleError("VMRequest sans ip_address — post-install impossible")

    wait_for_ssh(ip)

    pubkey = _automation_public_key()
    admin_hash = _admin_password_hash()
    collections_env = {"ANSIBLE_COLLECTIONS_PATH": str(repo_dir / ".ansible-collections")}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Étape 1 : root/mot de passe (bootstrap cloud-init éphémère) -> crée le compte
        # admin, y déploie notre clé publique d'automatisation, sudo avec mot de passe.
        inv_new = tmp_path / "inventory-new.ini"
        _write_inventory(inv_new, "new_vms", ip, {
            "ansible_user": "root",
            "ansible_password": bootstrap_root_password,
            "ansible_ssh_common_args": f"'{_SSH_COMMON_ARGS}'",
            # On est déjà root sur cette connexion — pas besoin de sudo (et Debian ne
            # donne pas de sudo root sans mot de passe par défaut, pam_rootok absent).
            "ansible_become": "false",
        })
        extra_vars_1 = tmp_path / "extra-vars-1.json"
        extra_vars_1.write_text(json.dumps({
            "admin_username": settings.ansible_admin_username,
            "admin_ssh_public_keys": [pubkey],
            "admin_password_hash": admin_hash,
            "approved_by": vm_request.approved_by,
        }))
        r1 = _run(
            [
                "ansible-playbook", "-i", str(inv_new), "vm-hardening/deploy-ssh-keys.yml",
                "-e", f"@{extra_vars_1}",
            ],
            cwd=repo_dir, timeout=300, env=collections_env,
        )
        if r1.returncode != 0:
            raise AnsibleError("deploy-ssh-keys.yml a échoué", r1.stdout, r1.stderr)
        logger.info("deploy-ssh-keys.yml OK pour %s (%s)", vm_request.hostname, ip)

        # Étape 2 : compte admin par clé -> ferme root en SSH pour de bon.
        inv_vms = tmp_path / "inventory-vms.ini"
        _write_inventory(inv_vms, "vms", ip, {
            "ansible_user": settings.ansible_admin_username,
            "ansible_ssh_private_key_file": settings.ansible_ssh_private_key_path,
            "ansible_ssh_common_args": f"'{_SSH_COMMON_ARGS}'",
        })
        extra_vars_2 = tmp_path / "extra-vars-2.json"
        extra_vars_2.write_text(json.dumps({
            "admin_username": settings.ansible_admin_username,
            "admin_ssh_public_keys": [pubkey],
            "ansible_become_pass": settings.ansible_admin_password,
            "approved_by": vm_request.approved_by,
        }))
        r2 = _run(
            [
                "ansible-playbook", "-i", str(inv_vms), "vm-hardening/harden-root.yml",
                "-e", f"@{extra_vars_2}",
            ],
            cwd=repo_dir, timeout=300, env=collections_env,
        )
        if r2.returncode != 0:
            raise AnsibleError("harden-root.yml a échoué", r2.stdout, r2.stderr)
        logger.info("harden-root.yml OK pour %s (%s) — root fermé en SSH", vm_request.hostname, ip)

        # Étape 3 : playbooks du catalogue (obligatoires + optionnels cochés), sur le
        # même compte admin par clé que l'étape 2.
        extra_vars_3 = tmp_path / "extra-vars-3.json"
        extra_vars_3.write_text(json.dumps({"approved_by": vm_request.approved_by}))
        for pb in (selected_playbooks or []):
            if not (repo_dir / pb.repo_path).exists():
                logger.warning(
                    "Playbook '%s' (%s) absent du repo — ignoré pour %s (catalogue en avance sur le contenu réel)",
                    pb.label, pb.repo_path, vm_request.hostname,
                )
                continue
            r3 = _run(
                ["ansible-playbook", "-i", str(inv_vms), "-e", f"@{extra_vars_3}", pb.repo_path],
                cwd=repo_dir, timeout=600, env=collections_env,
            )
            if r3.returncode != 0:
                raise AnsibleError(f"{pb.label} ({pb.repo_path}) a échoué", r3.stdout, r3.stderr)
            logger.info("%s OK pour %s (%s)", pb.label, vm_request.hostname, ip)


def run_adhoc_playbook_streaming(
    repo_path: str,
    limit_group: str | None,
    manual_targets: list[str],
    gitea_config: dict,
    on_output,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Exécute un playbook du catalogue à la demande, en direct — `on_output(chunk: str)`
    est appelé au fil de l'eau avec chaque nouvelle ligne produite par `ansible-playbook`
    (permet un suivi live côté dashboard, pas juste un résultat une fois terminé).

    Cible soit un groupe de l'inventaire persistant (app.provisioning.inventory_file),
    soit une liste de cibles manuelles (IP/nom DNS), soit les deux à la fois (union via
    `--limit "<groupe>:manual_targets"`). Compte admin par clé — ces hôtes sont déjà
    durcis (voir run_post_install), pas de bootstrap root ici.

    `dry_run` ajoute `--check --diff` (aucune modification réelle, juste ce qui
    changerait) ; `verbose` ajoute `-vvvv` (debug complet de la connexion/exécution).

    Lève AnsibleError si le playbook échoue, n'existe pas dans le repo, ou si aucune
    cible n'est fournie.
    """
    repo_dir = sync_playbooks_repo(gitea_config)
    if not (repo_dir / repo_path).exists():
        raise AnsibleError(f"Playbook introuvable dans le repo : {repo_path}")
    if not limit_group and not manual_targets:
        raise AnsibleError("Aucune cible sélectionnée (ni groupe, ni cible manuelle)")

    collections_env = {"ANSIBLE_COLLECTIONS_PATH": str(repo_dir / ".ansible-collections")}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Vars de connexion, appliquées à tout hôte quelle que soit sa source d'inventaire
        # (persistant ou manuel) — le fichier persistant lui-même ne décrit que les hôtes,
        # jamais les identifiants.
        conn_vars = tmp_path / "connection-vars.ini"
        conn_vars.write_text(
            "[all:vars]\n"
            f"ansible_user={settings.ansible_admin_username}\n"
            f"ansible_ssh_private_key_file={settings.ansible_ssh_private_key_path}\n"
            f"ansible_ssh_common_args='{_SSH_COMMON_ARGS}'\n"
            f"ansible_become_pass={settings.ansible_admin_password}\n"
        )

        cmd = ["ansible-playbook", "-i", str(INVENTORY_PATH), "-i", str(conn_vars)]

        limit_parts = [limit_group] if limit_group else []
        if manual_targets:
            manual_inv = tmp_path / "manual-targets.ini"
            _write_inventory_multi(manual_inv, "manual_targets", manual_targets, {})
            cmd += ["-i", str(manual_inv)]
            limit_parts.append("manual_targets")
        if limit_parts:
            cmd += ["--limit", ":".join(limit_parts)]

        if dry_run:
            cmd += ["--check", "--diff"]
        if verbose:
            cmd.append("-vvvv")

        cmd.append(repo_path)

        _run_streaming(cmd, cwd=repo_dir, env=collections_env, on_output=on_output)
