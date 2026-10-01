import logging
import random

from app.dns.base import DnsProvider

from ..base import IpamProvider, IpReservation

logger = logging.getLogger(__name__)


class EfficientIPProvider(IpamProvider, DnsProvider):
    """Stub — à remplacer par de vrais appels à l'API REST EfficientIP SOLIDserver.

    EfficientIP est une solution "tout-en-un" (IPAM + DHCP + DNS dans le même système) :
    ce provider implémente donc les deux interfaces (IpamProvider ET DnsProvider), au lieu
    d'avoir besoin d'une connexion DNS séparée comme pour une architecture disjointe
    (phpIPAM + Windows DNS, par ex.).
    """

    def reserve_ip(self, vlan: str, hostname: str) -> IpReservation:
        fake_ip = f"10.99.{random.randint(0, 254)}.{random.randint(1, 254)}"
        reservation = IpReservation(
            ip=fake_ip,
            hostname=hostname,
            vlan=vlan,
            gateway=f"10.99.{random.randint(0, 254)}.1",
            dns_servers=["10.99.0.1"],
            reservation_id=f"stub-eip-{hostname}",
        )
        logger.info(
            "[EfficientIP][stub] reserve_ip(vlan=%s, hostname=%s) -> %s",
            vlan,
            hostname,
            reservation,
        )
        return reservation

    def release_ip(self, ip: str) -> None:
        logger.info("[EfficientIP][stub] release_ip(ip=%s)", ip)

    def create_record(self, hostname: str, ip: str) -> None:
        logger.info("[EfficientIP][stub] create_record(hostname=%s, ip=%s)", hostname, ip)

    def delete_record(self, hostname: str, ip: str) -> None:
        logger.info("[EfficientIP][stub] delete_record(hostname=%s, ip=%s)", hostname, ip)
