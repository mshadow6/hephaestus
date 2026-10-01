"""Inventaire Ansible persistant, au format YAML natif — éditable en clair depuis le
dashboard (/playbooks/inventory), comme un inventaire statique Semaphore. Pensé pour ne
pas dépendre de cases à cocher une par une (invivable à l'échelle : "j'ai 300 VM, je dois
les sélectionner à la main" — retour user) : on cible un GROUPE (ou "all"), Ansible fait
la résolution lui-même via -i/--limit.
"""
import logging
from pathlib import Path

import yaml

from app.provisioning.inventory import discover_proxmox_vms

logger = logging.getLogger(__name__)

INVENTORY_PATH = Path("/app/config/inventory.yml")

DEFAULT_INVENTORY = """\
# Inventaire Ansible — édité ici ou régénéré (fusion additive) depuis la découverte
# Proxmox. Format YAML standard Ansible : https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html
all:
  children:
    decouverte:
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


def regenerate_from_proxmox(existing_done_vms: list[dict] | None = None) -> str:
    """Fusionne les VMs découvertes sur Proxmox (+ celles de ce pipeline) dans
    l'inventaire existant — additif seulement : n'écrase jamais un hôte déjà présent
    (potentiellement édité à la main avec une vraie IP), ajoute juste les nouveaux dans
    le groupe "decouverte"."""
    try:
        parsed = yaml.safe_load(read_inventory()) or {}
    except yaml.YAMLError:
        parsed = {}
    if not isinstance(parsed, dict) or "all" not in parsed:
        parsed = yaml.safe_load(DEFAULT_INVENTORY)

    all_node = parsed.setdefault("all", {})
    children = all_node.setdefault("children", {})
    decouverte = children.setdefault("decouverte", {})
    hosts = decouverte.setdefault("hosts", {}) or {}

    known_hostnames = set(hosts.keys())
    for group in children.values():
        known_hostnames.update((group or {}).get("hosts", {}).keys())

    discovered = discover_proxmox_vms()
    for vm in discovered:
        if vm["hostname"] in known_hostnames:
            continue
        hosts[vm["hostname"]] = {"ansible_host": vm["ip"]} if vm["ip"] else {}
        known_hostnames.add(vm["hostname"])

    for vm in (existing_done_vms or []):
        if vm["hostname"] in known_hostnames:
            continue
        hosts[vm["hostname"]] = {"ansible_host": vm["ip"]} if vm.get("ip") else {}
        known_hostnames.add(vm["hostname"])

    decouverte["hosts"] = hosts
    new_content = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True)
    write_inventory(new_content)
    return new_content
