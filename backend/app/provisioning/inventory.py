"""Inventaire dynamique de l'infra réelle — pas juste les VMs créées par ce pipeline
(VMRequest). Interroge TOUTES les connexions "hypervisor" activées (pas seulement celle
"active" utilisée pour le provisioning — plusieurs hôtes Proxmox physiques distincts
peuvent coexister, voir app.connections.store), pour lister leurs VMs existantes.
"""
import logging

import httpx

from app.connections.store import list_connections

logger = logging.getLogger(__name__)


def discover_proxmox_vms() -> list[dict]:
    """Retourne les VMs de tous les hôtes Proxmox connectés (connexions type=proxmox,
    activées). Chaque entrée : {hostname, ip (ou None), source, vmid, node, status}.

    Best-effort sur l'IP : tentée via l'agent QEMU invité (network-get-interfaces) —
    absent/désactivé sur beaucoup de VMs qui n'ont pas été créées par ce pipeline, dans
    ce cas ip=None et l'appelant doit laisser l'utilisateur la saisir à la main.
    """
    results: list[dict] = []
    proxmox_conns = [c for c in list_connections() if c.enabled and c.type == "proxmox"]

    for conn in proxmox_conns:
        api_url = conn.config.get("api_url", "").rstrip("/")
        token = conn.config.get("api_token", "")
        insecure = bool(conn.config.get("insecure_tls"))
        if not api_url or not token:
            continue

        headers = {"Authorization": f"PVEAPIToken={token}"}
        try:
            with httpx.Client(verify=not insecure, timeout=8, headers=headers) as client:
                nodes_resp = client.get(f"{api_url}/api2/json/nodes")
                nodes_resp.raise_for_status()
                nodes = [n["node"] for n in nodes_resp.json().get("data", [])]

                for node in nodes:
                    vms_resp = client.get(f"{api_url}/api2/json/nodes/{node}/qemu")
                    vms_resp.raise_for_status()
                    for vm in vms_resp.json().get("data", []):
                        if vm.get("template"):
                            continue  # jamais une cible de déploiement
                        ip = _try_get_ip(client, api_url, node, vm["vmid"])
                        results.append({
                            "hostname": vm.get("name", f"vmid-{vm['vmid']}"),
                            "ip": ip,
                            "source": conn.name,
                            "vmid": vm["vmid"],
                            "node": node,
                            "status": vm.get("status", "?"),
                        })
        except httpx.HTTPError as exc:
            logger.warning("Découverte Proxmox échouée pour la connexion '%s' : %s", conn.name, exc)
            continue

    return results


def discover_proxmox_templates() -> list[dict]:
    """Templates disponibles sur toutes les connexions Proxmox activées — pour peupler
    une liste déroulante côté formulaire de demande de VM plutôt que de laisser taper un
    nom à la main (source d'erreurs : un nom qui ne correspond à rien fait échouer la
    création bien plus tard, au moment du terraform apply)."""
    results: list[dict] = []
    proxmox_conns = [c for c in list_connections() if c.enabled and c.type == "proxmox"]
    for conn in proxmox_conns:
        api_url = conn.config.get("api_url", "").rstrip("/")
        token = conn.config.get("api_token", "")
        insecure = bool(conn.config.get("insecure_tls"))
        if not api_url or not token:
            continue
        try:
            results.extend(_list_templates(conn.name, api_url, token, insecure))
        except httpx.HTTPError as exc:
            logger.warning("Liste des templates Proxmox échouée pour la connexion '%s' : %s", conn.name, exc)
    return results


def resolve_template_vmid(proxmox_config: dict, template_name: str) -> tuple[int, str] | None:
    """Pour LA connexion utilisée à la création (pas toutes) — renvoie (vmid, node) du
    template nommé `template_name`, ou None si introuvable. Le node renvoyé est celui où
    vit réellement le template (pas une valeur de config statique) : le clone Proxmox se
    fait sur le même node que le template, donc s'y fier est plus correct qu'un node fixe
    configuré par ailleurs sur la connexion."""
    api_url = proxmox_config.get("api_url", "").rstrip("/")
    token = proxmox_config.get("api_token", "")
    insecure = bool(proxmox_config.get("insecure_tls"))
    if not api_url or not token:
        return None
    try:
        templates = _list_templates("_", api_url, token, insecure)
    except httpx.HTTPError as exc:
        logger.warning("Résolution du template '%s' échouée : %s", template_name, exc)
        return None
    for t in templates:
        if t["name"] == template_name:
            return t["vmid"], t["node"]
    return None


def _list_templates(conn_name: str, api_url: str, token: str, insecure: bool) -> list[dict]:
    headers = {"Authorization": f"PVEAPIToken={token}"}
    results: list[dict] = []
    with httpx.Client(verify=not insecure, timeout=8, headers=headers) as client:
        nodes_resp = client.get(f"{api_url}/api2/json/nodes")
        nodes_resp.raise_for_status()
        nodes = [n["node"] for n in nodes_resp.json().get("data", [])]
        for node in nodes:
            vms_resp = client.get(f"{api_url}/api2/json/nodes/{node}/qemu")
            vms_resp.raise_for_status()
            for vm in vms_resp.json().get("data", []):
                if not vm.get("template"):
                    continue
                results.append({
                    "name": vm.get("name", f"vmid-{vm['vmid']}"),
                    "vmid": vm["vmid"],
                    "node": node,
                    "source": conn_name,
                })
    return results


def _try_get_ip(client: httpx.Client, api_url: str, node: str, vmid: int) -> str | None:
    try:
        resp = client.get(
            f"{api_url}/api2/json/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces",
            timeout=3,
        )
        if resp.status_code != 200:
            return None
        for iface in resp.json().get("data", {}).get("result", []):
            if iface.get("name") == "lo":
                continue
            for addr in iface.get("ip-addresses", []):
                if addr.get("ip-address-type") == "ipv4" and not addr["ip-address"].startswith("127."):
                    return addr["ip-address"]
    except httpx.HTTPError:
        return None
    return None
