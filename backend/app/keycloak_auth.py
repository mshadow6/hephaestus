"""Authentification Keycloak (OIDC) — même principe que app.ldap_auth : config YAML
locale (jamais commitée), activable en complément des comptes locaux/LDAP, premier login
réussi crée un compte "fantôme" (voir app.routers.auth)."""
from pathlib import Path

import httpx
import yaml
from authlib.integrations.starlette_client import OAuth

CONFIG_PATH = Path("/app/config/keycloak.yaml")

DEFAULT_CONFIG = {
    "enabled": False,
    # URL du realm Keycloak, ex: https://keycloak.example.local/realms/hephaestus
    "issuer_url": "",
    "client_id": "",
    "client_secret": "",
    # Rôle attribué au premier login réussi — le plus restrictif par défaut, comme LDAP.
    "default_role": "viewer",
}


def load_keycloak_config() -> dict:
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {**DEFAULT_CONFIG, **data}


def save_keycloak_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))


def build_oauth_client(config: dict) -> OAuth:
    """Reconstruit un client OAuth à partir de la config courante à chaque usage (plutôt
    qu'un client unique figé au démarrage de l'app) — la config peut changer à chaud
    depuis /settings/keycloak, sans redémarrer le conteneur."""
    oauth = OAuth()
    oauth.register(
        name="keycloak",
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        server_metadata_url=f"{config['issuer_url'].rstrip('/')}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email"},
    )
    return oauth.keycloak


def test_keycloak_connection(config: dict) -> tuple[bool, str]:
    """Vérifie juste que le document de découverte OIDC est joignable et cohérent — pas
    un test d'authentification complet (impossible sans un navigateur pour suivre la
    redirection), même principe que le test LDAP (joignabilité + bind de service, pas
    d'auth utilisateur réelle)."""
    if not config.get("issuer_url"):
        return False, "URL du realm manquante."
    if not config.get("client_id") or not config.get("client_secret"):
        return False, "Client ID et client secret requis."
    discovery_url = f"{config['issuer_url'].rstrip('/')}/.well-known/openid-configuration"
    try:
        resp = httpx.get(discovery_url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        auth_endpoint = data.get("authorization_endpoint")
        if not auth_endpoint:
            return False, "Document de découverte OIDC invalide (authorization_endpoint absent)."
        return True, f"OK — realm joignable, authorization_endpoint : {auth_endpoint}"
    except httpx.HTTPError as exc:
        return False, f"Erreur réseau : {exc}"
    except ValueError:
        return False, "Réponse invalide (pas du JSON) à l'URL de découverte OIDC."
