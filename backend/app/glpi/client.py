import logging

import httpx

from app.errors import describe_connector_error

logger = logging.getLogger(__name__)


class GlpiError(RuntimeError):
    pass


def _get_token(config: dict) -> str:
    """OAuth2 "password" grant — API v2 de GLPI (≠ ancienne apirest.php App-Token/User-Token).
    Le client OAuth2 doit être créé côté GLPI (Configurer > Général > Clients OAuth) avec
    le grant type "Mot de passe" et les scopes api/user autorisés.

    Pas de cache de token ici : ce client n'est appelé qu'à l'approve/reject d'une VM
    (peu fréquent), la simplicité l'emporte sur l'économie d'un aller-retour HTTP.
    """
    base_url = config.get("base_url", "").rstrip("/")
    try:
        resp = httpx.post(
            f"{base_url}/api.php/token",
            data={
                "grant_type": "password",
                "client_id": config.get("client_id", ""),
                "client_secret": config.get("client_secret", ""),
                "username": config.get("username", ""),
                "password": config.get("password", ""),
                "scope": "api user",
            },
            timeout=10,
        )
    except httpx.HTTPError as exc:
        # Sans ce wrapping, une panne réseau (timeout, DNS, connexion refusée) remontait
        # en httpx.HTTPError brut — pas attrapé par le `except GlpiError` de
        # dashboard._notify_glpi(), qui plantait l'approbation/rejet de la VM alors que
        # le but affiché était justement de ne jamais bloquer dessus pour un souci GLPI.
        raise GlpiError(f"Authentification GLPI échouée : {describe_connector_error(exc)}") from exc
    data = resp.json()
    if "access_token" not in data:
        raise GlpiError(f"Authentification GLPI échouée : {data.get('error_description', data)}")
    return data["access_token"]


def add_change_followup(config: dict, ticket_id: str, content: str) -> None:
    """Poste un suivi (commentaire) sur un Changement GLPI. Lève GlpiError en cas
    d'échec — l'appelant décide s'il doit bloquer ou juste logger (voir dashboard.py :
    on ne bloque jamais une approbation/rejet à cause d'un souci côté GLPI)."""
    base_url = config.get("base_url", "").rstrip("/")
    token = _get_token(config)
    try:
        resp = httpx.post(
            f"{base_url}/api.php/v2.3/Assistance/Change/{ticket_id}/Timeline/Followup",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": content},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        raise GlpiError(f"Ajout du suivi GLPI échoué : {describe_connector_error(exc)}") from exc
    if resp.status_code >= 300:
        raise GlpiError(f"Ajout du suivi GLPI échoué (HTTP {resp.status_code}) : {resp.text[:300]}")
    logger.info("[GLPI] Suivi ajouté sur le changement #%s", ticket_id)
