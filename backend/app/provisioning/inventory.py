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


def discover_vsphere_vms() -> list[dict]:
    """VMs (pas les templates) de toutes les connexions VMware activées — même intention
    que discover_proxmox_vms, pour l'inventaire Ansible. L'IP vient de VMware Tools
    (item.guest.ipAddress) : même principe que l'agent QEMU côté Proxmox, absent/vide si
    les Tools ne sont pas installés ou pas encore démarrés sur la VM."""
    results: list[dict] = []
    vmware_conns = [c for c in list_connections() if c.enabled and c.type == "vmware"]
    for conn in vmware_conns:
        cfg = conn.config
        if not all([cfg.get("api_url"), cfg.get("username"), cfg.get("password")]):
            continue
        try:
            from pyVim.connect import Disconnect, SmartConnect
            from pyVmomi import vim
            import ssl

            host = cfg["api_url"].replace("https://", "").replace("http://", "").rstrip("/").split("/")[0]
            context = ssl._create_unverified_context() if cfg.get("insecure_tls", True) else None
            si = SmartConnect(host=host, user=cfg["username"], pwd=cfg["password"], port=443, sslContext=context)
            try:
                content = si.RetrieveContent()

                def find_datacenters(folder):
                    found = []
                    for item in folder.childEntity:
                        if isinstance(item, vim.Datacenter):
                            found.append(item)
                        elif isinstance(item, vim.Folder):
                            found.extend(find_datacenters(item))
                    return found

                def walk_vm(folder):
                    for item in folder.childEntity:
                        if isinstance(item, vim.VirtualMachine):
                            if item.config and item.config.template:
                                continue  # jamais une cible de déploiement
                            ip = None
                            dns_name = None
                            if item.guest:
                                if item.guest.ipAddress:
                                    ip = item.guest.ipAddress
                                # Nom DNS rapporté par VMware Tools (guest.hostName) —
                                # disponible sans redémarrage, utile pour cibler Ansible
                                # par nom plutôt que par IP si un DNS existe déjà.
                                if item.guest.hostName:
                                    dns_name = item.guest.hostName
                            results.append({
                                "hostname": item.name,
                                "ip": ip,
                                "dns_name": dns_name,
                                "source": conn.name,
                                "node": cfg.get("host", "vSphere"),
                                "status": str(item.runtime.powerState) if item.runtime else "?",
                            })
                        elif isinstance(item, vim.Folder):
                            walk_vm(item)

                for dc in find_datacenters(content.rootFolder):
                    walk_vm(dc.vmFolder)
            finally:
                Disconnect(si)
        except Exception as exc:  # noqa: BLE001 - pyVmomi lève des erreurs variées (SSL, DNS, auth...)
            logger.warning("Découverte VMware échouée pour la connexion '%s' : %s", conn.name, exc)
    return results


def discover_vsphere_templates() -> list[dict]:
    """Templates (VM marquées template) disponibles sur toutes les connexions VMware
    activées — même intention que discover_proxmox_templates, forme compatible
    ({name, node, source}) pour que /requests/new affiche les deux sans distinction."""
    results: list[dict] = []
    vmware_conns = [c for c in list_connections() if c.enabled and c.type == "vmware"]
    for conn in vmware_conns:
        cfg = conn.config
        if not all([cfg.get("api_url"), cfg.get("username"), cfg.get("password")]):
            continue
        try:
            from pyVim.connect import Disconnect, SmartConnect
            from pyVmomi import vim
            import ssl

            host = cfg["api_url"].replace("https://", "").replace("http://", "").rstrip("/").split("/")[0]
            context = ssl._create_unverified_context() if cfg.get("insecure_tls", True) else None
            si = SmartConnect(host=host, user=cfg["username"], pwd=cfg["password"], port=443, sslContext=context)
            try:
                content = si.RetrieveContent()

                def find_datacenters(folder):
                    found = []
                    for item in folder.childEntity:
                        if isinstance(item, vim.Datacenter):
                            found.append(item)
                        elif isinstance(item, vim.Folder):
                            found.extend(find_datacenters(item))
                    return found

                def walk_vm(folder):
                    for item in folder.childEntity:
                        if isinstance(item, vim.VirtualMachine) and item.config and item.config.template:
                            results.append({
                                "name": item.name, "node": cfg.get("host", "vSphere"),
                                "source": conn.name, "vmid": None,
                            })
                        elif isinstance(item, vim.Folder):
                            walk_vm(item)

                for dc in find_datacenters(content.rootFolder):
                    walk_vm(dc.vmFolder)
            finally:
                Disconnect(si)
        except Exception as exc:  # noqa: BLE001 - pyVmomi lève des erreurs variées (SSL, DNS, auth...)
            logger.warning("Liste des templates VMware échouée pour la connexion '%s' : %s", conn.name, exc)
    return results


def discover_templates() -> list[dict]:
    """Templates de TOUTES les connexions hyperviseur actives (plusieurs peuvent l'être
    en même temps - Proxmox + VMware, voir app.connections.store.MULTI_ACTIVE_CATEGORIES)
    - chaque entrée garde `source` (nom de connexion) pour que le formulaire associe
    chaque template à l'hyperviseur dont il vient, et ne propose jamais un template
    qu'on ne pourrait pas déployer avec la connexion choisie."""
    from app.connections.store import list_active_connections

    results: list[dict] = []
    for conn in list_active_connections("hypervisor"):
        if conn.type == "proxmox":
            results.extend([t for t in discover_proxmox_templates() if t["source"] == conn.name])
        elif conn.type == "vmware":
            results.extend([t for t in discover_vsphere_templates() if t["source"] == conn.name])
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
