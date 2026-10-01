import logging

from ..base import DnsProvider

logger = logging.getLogger(__name__)


class WindowsDnsProvider(DnsProvider):
    """Stub — à remplacer par de vrais appels au DNS intégré Active Directory (WinRM +
    PowerShell `Add-DnsServerResourceRecordA`/`Remove-DnsServerResourceRecord`, ou l'API
    REST DNS Server si exposée)."""

    def create_record(self, hostname: str, ip: str) -> None:
        logger.info("[WindowsDNS][stub] create_record(hostname=%s, ip=%s)", hostname, ip)

    def delete_record(self, hostname: str, ip: str) -> None:
        logger.info("[WindowsDNS][stub] delete_record(hostname=%s, ip=%s)", hostname, ip)
