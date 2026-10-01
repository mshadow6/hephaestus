from abc import ABC, abstractmethod


class DnsProvider(ABC):
    """Interface commune à tous les backends d'enregistrement DNS.

    Séparée de IpamProvider volontairement : sur une architecture "tout-en-un" (ex:
    EfficientIP), le même provider peut implémenter les deux interfaces. Sur une
    architecture disjointe (ex: phpIPAM pour l'IP + un DNS Windows/Technitium/BIND
    séparé), ce sont deux connexions différentes, configurées indépendamment — le worker
    appelle IpamProvider.reserve_ip() puis, séparément, DnsProvider.create_record() sur
    un connecteur potentiellement différent.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def create_record(self, hostname: str, ip: str) -> None:
        """Crée l'enregistrement DNS (A + PTR si possible) pour ce hostname/IP."""
        raise NotImplementedError

    @abstractmethod
    def delete_record(self, hostname: str, ip: str) -> None:
        """Supprime l'enregistrement DNS (rollback, décommissionnement)."""
        raise NotImplementedError
