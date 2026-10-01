import logging

from ..base import DnsProvider

logger = logging.getLogger(__name__)


class BindProvider(DnsProvider):
    """Stub — à remplacer par de vrais appels `nsupdate` (RFC 2136, avec clé TSIG)."""

    def create_record(self, hostname: str, ip: str) -> None:
        logger.info("[BIND][stub] create_record(hostname=%s, ip=%s)", hostname, ip)

    def delete_record(self, hostname: str, ip: str) -> None:
        logger.info("[BIND][stub] delete_record(hostname=%s, ip=%s)", hostname, ip)
