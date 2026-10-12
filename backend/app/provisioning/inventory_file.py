"""Inventaire Ansible persistant, au format YAML natif — éditable en clair depuis le
dashboard (/playbooks/inventory), comme un inventaire statique Semaphore. Pensé pour ne
pas dépendre de cases à cocher une par une (invivable à l'échelle : "j'ai 300 VM, je dois
les sélectionner à la main" — retour user) : on cible un GROUPE (ou "all"), Ansible fait
la résolution lui-même via -i/--limit.
"""
import logging
from pathlib import Path

import yaml

from app.provisioning.inventory import discover_proxmox_vms, discover_vsphere_vms

logger = logging.getLogger(__name__)

INVENTORY_PATH = Path("/app/config/inventory.yml")

# Groupes de découverte automatique, un par hyperviseur, plus un "inconnu" pour les
# hôtes de l'ancien groupe plat "decouverte" (avant cette séparation) qu'on ne peut pas
# rattacher avec certitude à Proxmox ou VMware après coup.
DISCOVERY_GROUPS = ["decouverte_proxmox", "decouverte_vmware", "decouverte_inconnu"]

DEFAULT_INVENTORY = """\
# Inventaire Ansible, édité ici ou régénéré (fusion additive) depuis la découverte
# Proxmox/VMware. Format YAML standard Ansible : https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html
all:
  children:
    decouverte_proxmox:
      hosts: {}
    decouverte_vmware:
      hosts: {}
    decouverte_inconnu:
      hosts: {}
"""


class InvalidInventoryError(ValueError):
    pass


def read_inventory() -> str:
    if not INVENTORY_PATH.exists():
        return DEFAULT_INVENTORY
    return INVENTORY_PATH.read_text()


def write_inventory(content: str) -> None:
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise InvalidInventoryError(f"YAML invalide : {exc}") from exc
    if not isinstance(parsed, dict) or "all" not in parsed:
        raise InvalidInventoryError("L'inventaire doit avoir une clé racine 'all' (format Ansible YAML).")
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_text(content)


def list_groups() -> list[str]:
    try:
        parsed = yaml.safe_load(read_inventory()) or {}
    except yaml.YAMLError:
        return []
    groups: list[str] = []

    def _walk(node: dict, prefix: str = ""):
        children = (node or {}).get("children") or {}
        for name in children:
            groups.append(name)
            _walk(children[name], name)

    _walk(parsed.get("all", {}))
    return groups


def list_structured() -> dict[str, list[dict]]:
    """Vue groupe -> liste de {hostname, ansible_host} — pour une interface de gestion
    hôtes/groupes façon Semaphore, plutôt que d'éditer le YAML brut à la main."""
    try:
        parsed = yaml.safe_load(read_inventory()) or {}
    except yaml.YAMLError:
        return {}
    children = ((parsed.get("all") or {}).get("children")) or {}
    result: dict[str, list[dict]] = {}
    for group, node in children.items():
        hosts = (node or {}).get("hosts") or {}
        result[group] = [
            {"hostname": h, "ansible_host": (v or {}).get("ansible_host")}
            for h, v in hosts.items()
        ]
    return result


def _load_or_default() -> dict:
    try:
        parsed = yaml.safe_load(read_inventory()) or {}
    except yaml.YAMLError:
        parsed = {}
    if not isinstance(parsed, dict) or "all" not in parsed:
        parsed = yaml.safe_load(DEFAULT_INVENTORY)
    parsed.setdefault("all", {}).setdefault("children", {})
    return parsed


def add_group(name: str) -> None:
    parsed = _load_or_default()
    children = parsed["all"]["children"]
    children.setdefault(name, {"hosts": {}})
    write_inventory(yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True))


def delete_group(name: str) -> None:
    parsed = _load_or_default()
    parsed["all"]["children"].pop(name, None)
    write_inventory(yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True))


def add_host(group: str, hostname: str, ansible_host: str = "") -> None:
    parsed = _load_or_default()
    children = parsed["all"]["children"]
    group_node = children.setdefault(group, {"hosts": {}})
    hosts = group_node.setdefault("hosts", {})
    hosts[hostname] = {"ansible_host": ansible_host} if ansible_host else {}
    write_inventory(yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True))


def delete_host(group: str, hostname: str) -> None:
    parsed = _load_or_default()
    children = parsed["all"]["children"]
    if group in children:
        (children[group].get("hosts") or {}).pop(hostname, None)
        write_inventory(yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True))


def _migrate_flat_decouverte(parsed: dict) -> None:
    """Migration unique : l'ancien groupe plat "decouverte" (avant la séparation par
    hyperviseur) part dans "decouverte_inconnu" — on ne peut pas savoir après coup s'il
    vient de Proxmox ou de VMware, la prochaine régénération le confirmera/corrigera si
    le même hôte est redécouvert avec une origine connue."""
    children = parsed["all"]["children"]
    old = children.pop("decouverte", None)
    if not old:
        return
    inconnu = children.setdefault("decouverte_inconnu", {}).setdefault("hosts", {})
    for hostname, data in (old.get("hosts") or {}).items():
        inconnu.setdefault(hostname, data)


def regenerate_discovery(existing_done_vms: list[dict] | None = None) -> str:
    """Fusionne les VMs découvertes sur Proxmox et VMware (+ celles de ce pipeline) dans
    l'inventaire existant — additif seulement : n'écrase jamais un hôte déjà présent
    (potentiellement édité à la main avec une vraie IP), ajoute juste les nouveaux dans
    le groupe de découverte correspondant à leur hyperviseur ("decouverte_proxmox" ou
    "decouverte_vmware").
    """
    try:
        parsed = yaml.safe_load(read_inventory()) or {}
    except yaml.YAMLError:
        parsed = {}
    if not isinstance(parsed, dict) or "all" not in parsed:
        parsed = yaml.safe_load(DEFAULT_INVENTORY)
    parsed.setdefault("all", {}).setdefault("children", {})

    _migrate_flat_decouverte(parsed)

    children = parsed["all"]["children"]
    known_hostnames = set()
    for group in children.values():
        known_hostnames.update((group or {}).get("hosts", {}).keys())

    def _merge(group_name: str, vms: list[dict]):
        hosts = children.setdefault(group_name, {}).setdefault("hosts", {})
        for vm in vms:
            if vm["hostname"] in known_hostnames:
                continue
            # IP d'abord si connue (agent QEMU/VMware Tools), sinon le nom DNS rapporté
            # par VMware Tools (guest.hostName) - utilisable tel quel par Ansible, résolu
            # à la connexion, sans jamais avoir besoin de l'agent ni d'un redémarrage.
            target = vm.get("ip") or vm.get("dns_name")
            hosts[vm["hostname"]] = {"ansible_host": target} if target else {}
            known_hostnames.add(vm["hostname"])

    _merge("decouverte_proxmox", discover_proxmox_vms())
    _merge("decouverte_vmware", discover_vsphere_vms())
    _merge("decouverte_inconnu", existing_done_vms or [])

    new_content = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True)
    write_inventory(new_content)
    return new_content


# Quel identifiant Ansible (nom d'une connexion type="ansible_credential", voir
# app.connections.types) utiliser pour chaque groupe — fichier séparé plutôt qu'une clé
# dans inventory.yml : ça garde l'inventaire lisible comme du YAML Ansible pur (pas de
# clé "maison" mélangée dedans), et un éventuel "YAML invalide" sur l'un des deux fichiers
# ne casse jamais l'autre.
CREDENTIALS_PATH = Path("/app/config/inventory_credentials.yml")


def _read_credentials_map() -> dict[str, str]:
    if not CREDENTIALS_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CREDENTIALS_PATH.read_text()) or {}
    except yaml.YAMLError:
        logger.warning("inventory_credentials.yml invalide, ignoré")
        return {}


def get_group_credential(group: str) -> str | None:
    return _read_credentials_map().get(group)


def set_group_credential(group: str, credential_name: str | None) -> None:
    mapping = _read_credentials_map()
    if credential_name:
        mapping[group] = credential_name
    else:
        mapping.pop(group, None)
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(yaml.safe_dump(mapping, sort_keys=True, allow_unicode=True))
