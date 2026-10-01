from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class IpReservation:
    ip: str
    hostname: str
    vlan: str
    netmask: str = "24"  # longueur de préfixe CIDR (ex: "24" pour /24)
    gateway: str | None = None
    dns_servers: list[str] | None = None
    reservation_id: str | None = None


class IpamProvider(ABC):
    """Interface commune à tous les backends d'allocation IP (EfficientIP, phpIPAM,
    Microsoft IPAM, ...). Ne fait QUE de l'allocation d'IP — l'enregistrement DNS est un
    souci séparé (voir app.dns.DnsProvider), car ce n'est pas toujours le même système
    (ex: phpIPAM pour l'IP + un DNS Windows/Technitium/BIND séparé pour le nom). Un
    provider "tout-en-un" type EfficientIP peut implémenter les deux interfaces.
    """

    def __init__(self, config: dict | None = None):
        # `config` vient de la connexion active (voir app.connections.store) : URL de
        # base, identifiants, etc. — vide pour un provider stub qui n'appelle encore rien.
        self.config = config or {}

    @abstractmethod
    def reserve_ip(self, vlan: str, hostname: str) -> IpReservation:
        """Réserve une adresse IP libre dans le VLAN donné pour ce hostname."""
        raise NotImplementedError

    @abstractmethod
    def release_ip(self, ip: str) -> None:
        """Libère une IP précédemment réservée (rollback, décommissionnement)."""
        raise NotImplementedError
