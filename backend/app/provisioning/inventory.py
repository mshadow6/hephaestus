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
