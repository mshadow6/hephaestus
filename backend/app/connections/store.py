import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.connections.types import PROVIDER_TYPES
from app.secrets_crypto import decrypt_value, encrypt_value

CONFIG_DIR = Path("/app/config/providers")

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{1,62}$")


class InvalidConnectionName(ValueError):
    pass


@dataclass
class Connection:
    name: str
    type: str
    enabled: bool = True
    active: bool = False
    config: dict = field(default_factory=dict)


def _path_for(name: str) -> Path:
    if not _SLUG_RE.match(name):
        raise InvalidConnectionName(
            "Le nom doit faire 2 à 63 caractères, lettres/chiffres/tirets uniquement, "
            "et commencer par une lettre ou un chiffre."
        )
    return CONFIG_DIR / f"{name}.yaml"


def _decrypt_config(provider_type: str, config: dict) -> dict:
    """Déchiffre les champs marqués `input_type="password"` pour ce type de provider —
    les autres champs (URL, nom d'utilisateur...) ne sont jamais chiffrés, pas besoin."""
    provider = PROVIDER_TYPES.get(provider_type)
    if provider is None:
        return config
    password_fields = {f.name for f in provider.fields if f.input_type == "password"}
    return {
        k: (decrypt_value(v) if k in password_fields and isinstance(v, str) else v)
        for k, v in config.items()
    }


def list_connections() -> list[Connection]:
    if not CONFIG_DIR.exists():
        return []
    connections = []
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        provider_type = data.get("type", "?")
        connections.append(
            Connection(
                name=path.stem,
                type=provider_type,
                enabled=data.get("enabled", True),
                active=data.get("active", False),
                config=_decrypt_config(provider_type, data.get("config", {})),
            )
        )
    return connections


def get_connection(name: str) -> Connection | None:
    path = _path_for(name)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    provider_type = data.get("type", "?")
    return Connection(
        name=name,
        type=provider_type,
        enabled=data.get("enabled", True),
        active=data.get("active", False),
        config=_decrypt_config(provider_type, data.get("config", {})),
    )


#  "hypervisor" fait exception à la règle "une seule active par catégorie" : contrairement
# à l'IPAM/DNS (une VM n'a qu'une seule source d'IP/DNS possible), plusieurs hyperviseurs
# différents (Proxmox + VMware, par ex.) doivent pouvoir être actifs en même temps, chaque
# VMRequest choisissant explicitement sa cible (voir VMRequest.hypervisor_connection) -
# demande explicite du user, pas une limitation technique à contourner.
MULTI_ACTIVE_CATEGORIES = {"hypervisor"}


def save_connection(connection: Connection) -> None:
    """Enregistre la connexion. Si `active=True`, désactive les autres connexions de la
    même catégorie (une seule connexion active par catégorie à la fois), **sauf** pour
    les catégories de `MULTI_ACTIVE_CATEGORIES` où plusieurs connexions peuvent être
    actives simultanément — c'est elle (ou elles) que le pipeline utilise, voir
    get_active_connection/list_active_connections.

    Écrit d'abord LA NOUVELLE connexion, désactive les autres ensuite seulement si ça a
    réussi — sinon un nom invalide (ou toute autre erreur d'écriture) désactivait déjà
    l'ancienne connexion active avant de planter sur la nouvelle, laissant la catégorie
    sans aucune connexion active du tout (trouvé en conditions réelles : le connecteur
    Technitium DNS s'est retrouvé désactivé après une tentative ratée d'en créer un
    second avec un nom invalide)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    _write(connection)

    if connection.active:
        category = PROVIDER_TYPES[connection.type].category
        if category in MULTI_ACTIVE_CATEGORIES:
            return
        for other in list_connections():
            if other.name != connection.name and other.active:
                other_type = PROVIDER_TYPES.get(other.type)
                if other_type and other_type.category == category:
                    other.active = False
                    _write(other)


def _encrypt_config(provider_type: str, config: dict) -> dict:
    provider = PROVIDER_TYPES.get(provider_type)
    if provider is None:
        return config
    password_fields = {f.name for f in provider.fields if f.input_type == "password"}
    return {
        k: (encrypt_value(v) if k in password_fields and isinstance(v, str) else v)
        for k, v in config.items()
    }


def _write(connection: Connection) -> None:
    path = _path_for(connection.name)
    payload = {
        "type": connection.type,
        "enabled": connection.enabled,
        "active": connection.active,
        "config": _encrypt_config(connection.type, connection.config),
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def get_active_connection(category: str) -> Connection | None:
    """Retourne la connexion active+activée pour cette catégorie ("ipam", "dns",
    "hypervisor", "scm", "itsm"), ou None si aucune n'est configurée — c'est ce que le
    pipeline (worker) doit utiliser, jamais des identifiants lus en dur dans .env."""
    for conn in list_connections():
        if not conn.enabled or not conn.active:
            continue
        provider_type = PROVIDER_TYPES.get(conn.type)
        if provider_type and provider_type.category == category:
            return conn
    return None


def list_active_connections(category: str) -> list[Connection]:
    """Toutes les connexions actives+activées d'une catégorie - pertinent surtout pour
    `MULTI_ACTIVE_CATEGORIES` (hypervisor) où il peut légitimement y en avoir plusieurs à
    la fois ; pour les autres catégories, renvoie 0 ou 1 connexion comme get_active_connection."""
    found = []
    for conn in list_connections():
        if not conn.enabled or not conn.active:
            continue
        provider_type = PROVIDER_TYPES.get(conn.type)
        if provider_type and provider_type.category == category:
            found.append(conn)
    return found


def delete_connection(name: str) -> bool:
    path = _path_for(name)
    if not path.exists():
        return False
    path.unlink()
    return True
