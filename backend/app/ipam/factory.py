from enum import Enum

from .base import IpamProvider
from .providers.efficientip import EfficientIPProvider
from .providers.microsoft import MicrosoftIpamProvider
from .providers.phpipam import PhpIpamProvider


class IpamProviderName(str, Enum):
    efficientip = "efficientip"
    microsoft = "microsoft"
    phpipam = "phpipam"


_PROVIDERS: dict[IpamProviderName, type[IpamProvider]] = {
    IpamProviderName.efficientip: EfficientIPProvider,
    IpamProviderName.microsoft: MicrosoftIpamProvider,
    IpamProviderName.phpipam: PhpIpamProvider,
}


def get_ipam_provider(provider_name: str, config: dict | None = None) -> IpamProvider:
    """Instancie le provider IPAM désigné par `provider_name`, avec la config de la
    connexion active (voir app.connections.store.get_active_connection).

    Volontairement sans lecture d'env var/store ici : cette fabrique est une fonction
    pure de ses arguments, pour rester réutilisable telle quelle hors de cette
    application. C'est à l'appelant de décider d'où viennent le type et la config
    (connexion active, env var, fichier de config, etc.).
    """
    try:
        provider_enum = IpamProviderName(provider_name.lower())
    except ValueError as exc:
        valid = ", ".join(p.value for p in IpamProviderName)
        raise ValueError(
            f"Provider IPAM inconnu: '{provider_name}' (valeurs possibles: {valid})"
        ) from exc

    return _PROVIDERS[provider_enum](config)
