import ipaddress
import logging

import httpx

from ..base import IpamProvider, IpReservation

logger = logging.getLogger(__name__)


class PhpIpamError(RuntimeError):
    pass


class PhpIpamProvider(IpamProvider):
    """Vraie implémentation phpIPAM — API REST (app_id + login utilisateur -> token,
    voir https://phpipam.net/api-documentation/).

    N'utilise PAS `first_free` de phpIPAM tel quel : phpIPAM raisonne en sous-réseaux
    CIDR, alors que la plage réellement allouable ici est un intervalle arbitraire à
    l'intérieur du sous-réseau (`allocation_range_start`/`allocation_range_end` — le
    reste du /24 est occupé par le DHCP de la box et des appareils existants, jamais à
    toucher). Le provider récupère donc la liste des IP déjà utilisées dans le
    sous-réseau et calcule lui-même la première libre dans cet intervalle.

    Plusieurs VLAN/sous-réseaux possibles : `config["subnets"]` est une liste de
    `{"vlan", "gateway", "dns_servers", "allocation_range_start", "allocation_range_end"}`
    — `reserve_ip(vlan, ...)` choisit le sous-réseau dont `vlan` correspond exactement à
    celui de la demande (gérée via /connections/<nom>/subnets). Si `subnets` est absent
    (connexion existante au format précédent), les 4 champs plats de la connexion servent
    de sous-réseau unique, quel que soit le VLAN demandé — comportement inchangé pour les
    connexions déjà configurées avant l'ajout de cette fonctionnalité.
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.base_url = (self.config.get("base_url") or "").rstrip("/")
        self.app_id = self.config.get("app_id", "")
        self.username = self.config.get("username", "")
        self.password = self.config.get("password", "")
        self.subnets: list[dict] = self.config.get("subnets") or []

        missing = [
            k for k, v in {
                "base_url": self.base_url, "app_id": self.app_id,
                "username": self.username, "password": self.password,
            }.items() if not v
        ]
        if missing:
            raise PhpIpamError(f"Connexion phpIPAM incomplète — champs manquants : {', '.join(missing)}")

        if not self.subnets:
            # Format précédent, un seul sous-réseau au niveau de la connexion — repris
            # tel quel comme fallback, peu importe le VLAN demandé.
            flat = {
                "vlan": None,
                "gateway": self.config.get("gateway", ""),
                "dns_servers": self.config.get("dns_servers", ""),
                "allocation_range_start": self.config.get("allocation_range_start", ""),
                "allocation_range_end": self.config.get("allocation_range_end", ""),
            }
            flat_missing = [k for k in ("gateway", "allocation_range_start", "allocation_range_end") if not flat[k]]
            if flat_missing:
                raise PhpIpamError(
                    "Connexion phpIPAM incomplète — soit configure au moins un sous-réseau "
                    f"(/connections), soit renseigne : {', '.join(flat_missing)}"
                )
            self.subnets = [flat]

    def _resolve_subnet(self, vlan: str) -> dict:
        if len(self.subnets) == 1 and self.subnets[0].get("vlan") is None:
            return self.subnets[0]  # fallback ancien format — un seul sous-réseau, VLAN ignoré
        for subnet in self.subnets:
            if subnet.get("vlan") == vlan:
                return subnet
        known = ", ".join(s.get("vlan", "?") for s in self.subnets) or "aucun"
        raise PhpIpamError(
            f"Aucun sous-réseau phpIPAM configuré pour le VLAN '{vlan}' — VLAN connus sur "
            f"cette connexion : {known}. Ajoute-le sur /connections/<nom>/subnets."
        )

    def _api(self) -> str:
        return f"{self.base_url}/api/{self.app_id}"

    def _login(self, client: httpx.Client) -> str:
        resp = client.post(f"{self._api()}/user/", auth=(self.username, self.password))
        data = resp.json()
        if not data.get("success"):
            raise PhpIpamError(f"Authentification phpIPAM échouée : {data.get('message')}")
        return data["data"]["token"]

    def _subnet_id(self, client: httpx.Client, token: str, gateway: str) -> int:
        network = ipaddress.ip_network(f"{gateway}/24", strict=False)
        resp = client.get(f"{self._api()}/subnets/", headers={"Token": token})
        data = resp.json()
        if not data.get("success"):
            raise PhpIpamError(f"Impossible de lister les sous-réseaux phpIPAM : {data.get('message')}")
        for subnet in data.get("data") or []:
            if subnet["subnet"] == str(network.network_address) and str(subnet["mask"]) == str(network.prefixlen):
                return int(subnet["id"])
        raise PhpIpamError(
            f"Aucun sous-réseau phpIPAM ne correspond à {network} — crée-le d'abord dans phpIPAM."
        )

    def _subnet_for_ip(self, ip: str) -> dict:
        addr = ipaddress.ip_address(ip)
        for subnet in self.subnets:
            gateway = subnet.get("gateway")
            if gateway and addr in ipaddress.ip_network(f"{gateway}/24", strict=False):
                return subnet
        raise PhpIpamError(f"Aucun sous-réseau configuré ne contient l'IP {ip}.")

    def _used_ips(self, client: httpx.Client, token: str, subnet_id: int) -> set[str]:
        resp = client.get(f"{self._api()}/subnets/{subnet_id}/addresses/", headers={"Token": token})
        data = resp.json()
        if data.get("code") == 200 and not data.get("success"):
            raise PhpIpamError(f"Impossible de lister les adresses phpIPAM : {data.get('message')}")
        if data.get("code") == 404:
            return set()  # sous-réseau vide, aucune adresse encore enregistrée
        return {a["ip"] for a in (data.get("data") or [])}

    def reserve_ip(self, vlan: str, hostname: str) -> IpReservation:
        subnet = self._resolve_subnet(vlan)
        gateway = subnet["gateway"]
        dns_raw = subnet.get("dns_servers", "")
        dns_servers = [s.strip() for s in dns_raw.split(",") if s.strip()] if dns_raw else []
        range_start = subnet["allocation_range_start"]
        range_end = subnet["allocation_range_end"]

        with httpx.Client(timeout=10) as client:
            token = self._login(client)
            subnet_id = self._subnet_id(client, token, gateway)
            used = self._used_ips(client, token, subnet_id)

            start = ipaddress.ip_address(range_start)
            end = ipaddress.ip_address(range_end)
            candidate = None
            ip = start
            while ip <= end:
                if str(ip) not in used:
                    candidate = str(ip)
                    break
                ip += 1

            if candidate is None:
                raise PhpIpamError(
                    f"Plus aucune IP libre entre {range_start} et {range_end}."
                )

            resp = client.post(
                f"{self._api()}/addresses/",
                headers={"Token": token},
                data={
                    "subnetId": subnet_id,
                    "ip": candidate,
                    "hostname": hostname,
                    "description": f"VM provisionnée automatiquement (vlan={vlan})",
                },
            )
            data = resp.json()
            if not data.get("success"):
                raise PhpIpamError(f"Réservation phpIPAM échouée pour {candidate} : {data.get('message')}")
            address_id = data.get("id")

        reservation = IpReservation(
            ip=candidate,
            hostname=hostname,
            vlan=vlan,
            netmask="24",
            gateway=gateway,
            dns_servers=dns_servers or None,
            reservation_id=str(address_id) if address_id else None,
        )
        logger.info("[phpIPAM] reserve_ip(vlan=%s, hostname=%s) -> %s", vlan, hostname, reservation)
        return reservation

    def release_ip(self, ip: str) -> None:
        subnet = self._subnet_for_ip(ip)
        with httpx.Client(timeout=10) as client:
            token = self._login(client)
            subnet_id = self._subnet_id(client, token, subnet["gateway"])
            resp = client.delete(f"{self._api()}/addresses/{ip}/{subnet_id}/", headers={"Token": token})
            data = resp.json()
            if not data.get("success"):
                logger.warning("[phpIPAM] release_ip(%s) a échoué : %s", ip, data.get("message"))
            else:
                logger.info("[phpIPAM] release_ip(%s) OK", ip)
