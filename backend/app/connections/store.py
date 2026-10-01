import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.connections.types import PROVIDER_TYPES

CONFIG_DIR = Path("/app/config/providers")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


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
            "Le nom doit faire 2 à 63 caractères, minuscules/chiffres/tirets uniquement, "
            "et commencer par une lettre ou un chiffre."
        )
    return CONFIG_DIR / f"{name}.yaml"


def list_connections() -> list[Connection]:
    if not CONFIG_DIR.exists():
        return []
    connections = []
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        connections.append(
            Connection(
                name=path.stem,
                type=data.get("type", "?"),
                enabled=data.get("enabled", True),
                active=data.get("active", False),
                config=data.get("config", {}),
            )
        )
    return connections


def get_connection(name: str) -> Connection | None:
    path = _path_for(name)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    return Connection(
        name=name,
        type=data.get("type", "?"),
        enabled=data.get("enabled", True),
        active=data.get("active", False),
        config=data.get("config", {}),
    )


def save_connection(connection: Connection) -> None:
    """Enregistre la connexion. Si `active=True`, désactive les autres connexions de la
    même catégorie (une seule connexion active par catégorie à la fois — c'est elle que
    le pipeline utilise, voir get_active_connection)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if connection.active:
        category = PROVIDER_TYPES[connection.type].category
        for other in list_connections():
            if other.name != connection.name and other.active:
                other_type = PROVIDER_TYPES.get(other.type)
                if other_type and other_type.category == category:
                    other.active = False
                    _write(other)

    _write(connection)


def _write(connection: Connection) -> None:
    path = _path_for(connection.name)
    payload = {
        "type": connection.type,
        "enabled": connection.enabled,
        "active": connection.active,
        "config": connection.config,
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


def delete_connection(name: str) -> bool:
    path = _path_for(name)
    if not path.exists():
        return False
    path.unlink()
    return True
