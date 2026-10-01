import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.ipam.base import IpReservation
from app.models import VMRequest
from app.provisioning.inventory import resolve_template_vmid

logger = logging.getLogger(__name__)

MODULE_SOURCE_DIR = Path("/app/terraform/proxmox")
RUNS_BASE_DIR = Path("/app/terraform-runs")


class ProvisioningError(RuntimeError):
    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(message)


@dataclass
class TerraformResult:
    vm_id: int
    stdout: str
    stderr: str


def _run_dir(vm_request: VMRequest) -> Path:
    return RUNS_BASE_DIR / f"{vm_request.id}-{vm_request.hostname}"


def _sync_module(run_dir: Path) -> None:
    """Copie les fichiers .tf du module dans le dossier d'exécution dédié à cette VM.

    Ne touche pas à un éventuel .terraform/ ou terraform.tfstate déjà présent
    (retry d'une requête précédente) — seuls les fichiers .tf sont resynchronisés.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    for tf_file in MODULE_SOURCE_DIR.glob("*.tf"):
        shutil.copyfile(tf_file, run_dir / tf_file.name)


def _render_tfvars(
    vm_request: VMRequest, reservation: IpReservation, bootstrap_root_password: str, proxmox_config: dict
) -> dict:
    resolved = resolve_template_vmid(proxmox_config, vm_request.os_template or "")
    if resolved is None:
        raise ProvisioningError(
            f"Template Proxmox introuvable : '{vm_request.os_template}' — vérifie le nom exact "
            "sur le cluster (visible dans la liste déroulante de /requests/new, ou "
            "directement dans l'interface Proxmox : un template a l'icône grisée)."
        )
    template_vmid, template_node = resolved

    if not reservation.gateway:
        raise ProvisioningError(
            "La réservation IPAM n'a pas de gateway — impossible de configurer une IP "
            "statique (un serveur ne doit jamais dépendre du DHCP)."
        )

    return {
        # Node résolu depuis le template lui-même (pas une valeur fixe de la connexion) :
        # le clone Proxmox se fait sur le même node que le template, s'y fier est plus
        # sûr qu'un node configuré statiquement qui pourrait ne pas être le bon.
        "proxmox_node": template_node,
        "proxmox_insecure": proxmox_config.get("insecure_tls", True),
        "vm_name": vm_request.hostname,
        "vm_vcpu": vm_request.cpu,
        "vm_ram_mb": vm_request.ram_gb * 1024,
        "vm_disk_gb": vm_request.disk_gb,
        "template_vmid": template_vmid,
        "vlan_tag": int(vm_request.vlan),
        "network_bridge": proxmox_config.get("network_bridge") or "vmbr0",
        "disk_storage": proxmox_config.get("disk_storage") or "local-lvm",
        "disk_interface": proxmox_config.get("disk_interface") or "scsi0",
        "vm_ip_address": f"{reservation.ip}/{reservation.netmask}",
        "vm_gateway": reservation.gateway,
        "vm_dns_servers": reservation.dns_servers or [],
        "vm_bootstrap_root_password": bootstrap_root_password,
    }


def _terraform_env(proxmox_config: dict) -> dict[str, str]:
    env = os.environ.copy()
    env["TF_VAR_proxmox_api_url"] = proxmox_config["api_url"]
    env["TF_VAR_proxmox_api_token"] = proxmox_config["api_token"]
    env["TF_IN_AUTOMATION"] = "1"
    return env


def _run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    logger.info("Exécution: %s (cwd=%s)", " ".join(cmd), cwd)
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def apply_vm(
    vm_request: VMRequest, reservation: IpReservation, bootstrap_root_password: str, proxmox_config: dict
) -> TerraformResult:
    """Clone et configure la VM sur Proxmox via Terraform, avec IP statique (jamais de
    DHCP pour un serveur) — l'IP/masque/gateway/DNS viennent de la réservation IPAM.

    `proxmox_config` vient de la connexion "hypervisor" active (voir
    app.connections.store.get_active_connection) : api_url, api_token, node, etc.

    `bootstrap_root_password` est un mot de passe root éphémère (généré par l'appelant,
    différent à chaque requête) injecté via cloud-init : il ne sert qu'une fois, pour la
    première connexion Ansible (`deploy-ssh-keys.yml`), avant que `harden-root.yml` ne
    ferme root en SSH pour de bon.

    Lève ProvisioningError (avec stdout/stderr attachés) si terraform échoue.
    """
    run_dir = _run_dir(vm_request)
    _sync_module(run_dir)

    tfvars = _render_tfvars(vm_request, reservation, bootstrap_root_password, proxmox_config)
    (run_dir / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=2))

    env = _terraform_env(proxmox_config)

    init = _run(["terraform", "init", "-input=false", "-no-color"], run_dir, env, timeout=120)
    logger.info("terraform init stdout:\n%s", init.stdout)
    if init.returncode != 0:
        logger.error("terraform init stderr:\n%s", init.stderr)
        raise ProvisioningError(
            f"terraform init a échoué (code {init.returncode})", init.stdout, init.stderr
        )

    apply = _run(
        [
            "terraform", "apply", "-auto-approve", "-input=false", "-no-color",
            "-var-file=terraform.tfvars.json",
        ],
        run_dir, env, timeout=1800,
    )
    logger.info("terraform apply stdout:\n%s", apply.stdout)
    if apply.returncode != 0:
        logger.error("terraform apply stderr:\n%s", apply.stderr)
        raise ProvisioningError(
            f"terraform apply a échoué (code {apply.returncode})", apply.stdout, apply.stderr
        )

    output = _run(["terraform", "output", "-json", "vm_id"], run_dir, env, timeout=30)
    if output.returncode != 0:
        raise ProvisioningError(
            "terraform apply a réussi mais impossible de lire vm_id en sortie",
            output.stdout, output.stderr,
        )

    vm_id = json.loads(output.stdout)

    return TerraformResult(vm_id=vm_id, stdout=apply.stdout, stderr=apply.stderr)
