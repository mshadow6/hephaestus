from enum import Enum

from .base import DnsProvider
from .providers.bind import BindProvider
from .providers.technitium import TechnitiumProvider
from .providers.windows_dns import WindowsDnsProvider


class DnsProviderName(str, Enum):
    windows_dns = "windows_dns"
    technitium = "technitium"
    bind = "bind"


_PROVIDERS: dict[DnsProviderName, type[DnsProvider]] = {
    DnsProviderName.windows_dns: WindowsDnsProvider,
    DnsProviderName.technitium: TechnitiumProvider,
    DnsProviderName.bind: BindProvider,
}


def get_dns_provider(provider_name: str, config: dict | None = None) -> DnsProvider:
    """Instancie le provider DNS désigné par `provider_name`, avec la config de la
    connexion active. Fonction pure de ses arguments, même logique que
    app.ipam.factory.get_ipam_provider. Peut être `None` côté appelant si aucun DNS
    séparé n'est configuré (cas d'un IPAM tout-en-un qui gère déjà le DNS lui-même).
    """
    try:
        provider_enum = DnsProviderName(provider_name.lower())
    except ValueError as exc:
        valid = ", ".join(p.value for p in DnsProviderName)
        raise ValueError(
            f"Provider DNS inconnu: '{provider_name}' (valeurs possibles: {valid})"
        ) from exc

    return _PROVIDERS[provider_enum](config)
