import logging
import random

from ..base import IpamProvider, IpReservation

logger = logging.getLogger(__name__)


class MicrosoftIpamProvider(IpamProvider):
    """Stub — à remplacer par de vrais appels à l'API Microsoft IPAM (WMI/PowerShell ou
    REST). IP-only : le DNS Windows/AD est un souci séparé, voir
    app.dns.providers.windows_dns.WindowsDnsProvider.
    """

    def reserve_ip(self, vlan: str, hostname: str) -> IpReservation:
        fake_ip = f"10.98.{random.randint(0, 254)}.{random.randint(1, 254)}"
        reservation = IpReservation(
            ip=fake_ip,
            hostname=hostname,
            vlan=vlan,
            gateway=f"10.98.{random.randint(0, 254)}.1",
            reservation_id=f"stub-ms-{hostname}",
        )
        logger.info(
            "[MicrosoftIPAM][stub] reserve_ip(vlan=%s, hostname=%s) -> %s",
            vlan,
            hostname,
            reservation,
        )
        return reservation

    def release_ip(self, ip: str) -> None:
        logger.info("[MicrosoftIPAM][stub] release_ip(ip=%s)", ip)
