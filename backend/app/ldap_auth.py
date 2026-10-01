from pathlib import Path

import yaml
from ldap3 import ALL, Connection, Server
from ldap3.core.exceptions import LDAPException

CONFIG_PATH = Path("/app/config/ldap.yaml")

DEFAULT_CONFIG = {
    "enabled": False,
    "server": "",  # ex: ldaps://dc.example.local:636
    "use_tls": True,
    "bind_dn": "",  # compte de service pour la recherche, vide = bind anonyme
    "bind_password": "",
    "search_base": "",  # ex: ou=users,dc=example,dc=local
    "search_filter": "(uid={username})",  # (sAMAccountName={username}) pour Active Directory
}


def load_ldap_config() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {**DEFAULT_CONFIG, **data}


def save_ldap_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))


def _connect(config: dict) -> Connection:
    server = Server(config["server"], use_ssl=config["server"].startswith("ldaps://"), get_info=ALL)
    if config.get("bind_dn"):
        return Connection(server, user=config["bind_dn"], password=config.get("bind_password", ""))
    return Connection(server)


def test_ldap_connection(config: dict) -> tuple[bool, str]:
    """Vérifie juste la joignabilité + le bind de service (pas d'authentification utilisateur)."""
    if not config.get("server"):
        return False, "URL du serveur manquante."
    try:
        conn = _connect(config)
        if not conn.bind():
            return False, f"Connexion établie mais bind refusé : {conn.result.get('description', '?')}"
        conn.unbind()
        return True, "Connexion et bind réussis."
    except LDAPException as exc:
        return False, f"Erreur LDAP : {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Erreur : {exc}"


def authenticate(username: str, password: str) -> bool:
    """Recherche l'utilisateur puis tente un bind avec ses identifiants.

    Renvoie False si LDAP est désactivé, mal configuré, ou si l'authentification échoue —
    ne lève jamais d'exception, pour rester un simple fallback silencieux après l'échec
    de l'auth locale.
    """
    config = load_ldap_config()
    if not config.get("enabled") or not config.get("server") or not config.get("search_base"):
        return False

    try:
        search_conn = _connect(config)
        if not search_conn.bind():
            return False

        search_filter = config["search_filter"].format(username=username)
        search_conn.search(config["search_base"], search_filter, attributes=["dn"])
        if not search_conn.entries:
            search_conn.unbind()
            return False

        user_dn = search_conn.entries[0].entry_dn
        search_conn.unbind()

        user_conn = Connection(search_conn.server, user=user_dn, password=password)
        bound = user_conn.bind()
        if bound:
            user_conn.unbind()
        return bound
    except LDAPException:
        return False
    except Exception:  # noqa: BLE001
        return False
