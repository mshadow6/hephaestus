import logging

from ..base import DnsProvider

logger = logging.getLogger(__name__)


class TechnitiumProvider(DnsProvider):
    """Stub — à remplacer par de vrais appels à l'API REST Technitium DNS Server
    (/api/zones/records/add et /delete, authentifiés par token)."""

    def create_record(self, hostname: str, ip: str) -> None:
        logger.info("[Technitium][stub] create_record(hostname=%s, ip=%s)", hostname, ip)

    def delete_record(self, hostname: str, ip: str) -> None:
        logger.info("[Technitium][stub] delete_record(hostname=%s, ip=%s)", hostname, ip)
