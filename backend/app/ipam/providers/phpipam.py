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
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.base_url = (self.config.get("base_url") or "").rstrip("/")
        self.app_id = self.config.get("app_id", "")
        self.username = self.config.get("username", "")
        self.password = self.config.get("password", "")
        self.gateway = self.config.get("gateway", "")
        dns_raw = self.config.get("dns_servers", "")
        self.dns_servers = [s.strip() for s in dns_raw.split(",") if s.strip()] if dns_raw else []
        self.range_start = self.config.get("allocation_range_start", "")
        self.range_end = self.config.get("allocation_range_end", "")

        missing = [
            k for k, v in {
                "base_url": self.base_url, "app_id": self.app_id, "username": self.username,
                "password": self.password, "gateway": self.gateway,
                "allocation_range_start": self.range_start, "allocation_range_end": self.range_end,
            }.items() if not v
        ]
        if missing:
            raise PhpIpamError(f"Connexion phpIPAM incomplète — champs manquants : {', '.join(missing)}")

    def _api(self) -> str:
        return f"{self.base_url}/api/{self.app_id}"

    def _login(self, client: httpx.Client) -> str:
        resp = client.post(f"{self._api()}/user/", auth=(self.username, self.password))
        data = resp.json()
        if not data.get("success"):
            raise PhpIpamError(f"Authentification phpIPAM échouée : {data.get('message')}")
        return data["data"]["token"]

    def _subnet_id(self, client: httpx.Client, token: str) -> int:
        network = ipaddress.ip_network(f"{self.gateway}/24", strict=False)
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

    def _used_ips(self, client: httpx.Client, token: str, subnet_id: int) -> set[str]:
        resp = client.get(f"{self._api()}/subnets/{subnet_id}/addresses/", headers={"Token": token})
        data = resp.json()
        if data.get("code") == 200 and not data.get("success"):
            raise PhpIpamError(f"Impossible de lister les adresses phpIPAM : {data.get('message')}")
        if data.get("code") == 404:
            return set()  # sous-réseau vide, aucune adresse encore enregistrée
        return {a["ip"] for a in (data.get("data") or [])}

    def reserve_ip(self, vlan: str, hostname: str) -> IpReservation:
        with httpx.Client(timeout=10) as client:
            token = self._login(client)
            subnet_id = self._subnet_id(client, token)
            used = self._used_ips(client, token, subnet_id)

            start = ipaddress.ip_address(self.range_start)
            end = ipaddress.ip_address(self.range_end)
            candidate = None
            ip = start
            while ip <= end:
                if str(ip) not in used:
                    candidate = str(ip)
                    break
                ip += 1

            if candidate is None:
                raise PhpIpamError(
                    f"Plus aucune IP libre entre {self.range_start} et {self.range_end}."
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
            gateway=self.gateway,
            dns_servers=self.dns_servers or None,
            reservation_id=str(address_id) if address_id else None,
        )
        logger.info("[phpIPAM] reserve_ip(vlan=%s, hostname=%s) -> %s", vlan, hostname, reservation)
        return reservation

    def release_ip(self, ip: str) -> None:
        with httpx.Client(timeout=10) as client:
            token = self._login(client)
            subnet_id = self._subnet_id(client, token)
            resp = client.delete(f"{self._api()}/addresses/{ip}/{subnet_id}/", headers={"Token": token})
            data = resp.json()
            if not data.get("success"):
                logger.warning("[phpIPAM] release_ip(%s) a échoué : %s", ip, data.get("message"))
            else:
                logger.info("[phpIPAM] release_ip(%s) OK", ip)
