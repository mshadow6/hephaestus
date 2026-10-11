import logging

import httpx

from ..base import DnsProvider

logger = logging.getLogger(__name__)


class TechnitiumError(RuntimeError):
    pass


class TechnitiumProvider(DnsProvider):
    """Vraie implémentation Technitium DNS Server — API REST authentifiée par token
    (voir https://github.com/TechnitiumSoftware/DnsServer/blob/master/APIDOCS.md).

    Un token API se génère dans l'interface Technitium (Administration -> Sessions ->
    Create Token), pas le mot de passe admin lui-même — jamais de identifiants de
    connexion interactive stockés ici.
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.base_url = (self.config.get("base_url") or "").rstrip("/")
        self.api_token = self.config.get("api_token", "")
        self.zone = self.config.get("zone", "")
        missing = [
            k for k, v in {
                "base_url": self.base_url, "api_token": self.api_token, "zone": self.zone,
            }.items() if not v
        ]
        if missing:
            raise TechnitiumError(f"Connexion Technitium incomplète — champs manquants : {', '.join(missing)}")

    def _call(self, path: str, params: dict) -> dict:
        # Auth par header (Authorization: Bearer) plutôt que `?token=` dans l'URL — avec
        # le token en query string, httpx logue l'URL complète (avec le token en clair)
        # à chaque requête au niveau INFO, qui finit dans les logs persistants du worker
        # (`docker compose logs worker`). Confirmé en conditions réelles : Technitium
        # accepte bien les deux, le header n'apparaît jamais dans ce genre de log.
        url = f"{self.base_url}/api/{path}"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params=params, headers={"Authorization": f"Bearer {self.api_token}"})
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") != "ok":
            raise TechnitiumError(f"Technitium a renvoyé une erreur sur {path} : {data.get('errorMessage', data)}")
        return data

    def create_record(self, hostname: str, ip: str) -> None:
        domain = f"{hostname}.{self.zone}"
        self._call("zones/records/add", {
            "domain": domain,
            "zone": self.zone,
            "type": "A",
            "ipAddress": ip,
            "ttl": 3600,
            "overwrite": "true",
            # Crée aussi le PTR (et la zone inverse si besoin) dans la foulée — même
            # intention que les cases à cocher de l'UI web, pas de résolution inverse
            # bancale à rattraper plus tard pour chaque VM provisionnée.
            "ptr": "true",
            "createPtrZone": "true",
        })
        logger.info("[Technitium] create_record(%s -> %s) OK", domain, ip)

    def delete_record(self, hostname: str, ip: str) -> None:
        domain = f"{hostname}.{self.zone}"
        self._call("zones/records/delete", {
            "domain": domain,
            "zone": self.zone,
            "type": "A",
            "ipAddress": ip,
        })
        logger.info("[Technitium] delete_record(%s -> %s) OK", domain, ip)
