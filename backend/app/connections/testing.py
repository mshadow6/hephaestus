import asyncio
import subprocess

import httpx

from app.config import settings
from app.errors import describe_connector_error
from app.glpi.client import GlpiError, _get_token

from .store import Connection


async def test_connection(connection: Connection) -> tuple[bool, str]:
    """Best-effort connectivity test. Returns (success, message)."""
    cfg = connection.config

    if connection.type == "proxmox":
        return await _test_proxmox(cfg)
    if connection.type == "phpipam":
        return await _test_phpipam(cfg)
    if connection.type == "glpi":
        return await _test_glpi(cfg)
    if connection.type == "gitea":
        return await asyncio.to_thread(_test_gitea, cfg)

    url = cfg.get("base_url") or cfg.get("api_url")
    if not url:
        return False, "Aucune URL renseignée pour cette connexion."
    return await _test_http_reachable(url, insecure=bool(cfg.get("insecure_tls")))


async def _test_proxmox(cfg: dict) -> tuple[bool, str]:
    api_url = cfg.get("api_url")
    token = cfg.get("api_token")
    if not api_url or not token:
        return False, "URL API et token requis."
    try:
        async with httpx.AsyncClient(verify=not cfg.get("insecure_tls"), timeout=8) as client:
            resp = await client.get(
                f"{api_url.rstrip('/')}/api2/json/version",
                headers={"Authorization": f"PVEAPIToken={token}"},
            )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return True, f"OK — Proxmox VE {data.get('version', '?')}"
        if resp.status_code == 401:
            return False, "401 Unauthorized — token invalide ou realm incorrect."
        return False, f"HTTP {resp.status_code} — {resp.text[:200]}"
    except httpx.HTTPError as exc:
        return False, describe_connector_error(exc)


async def _test_phpipam(cfg: dict) -> tuple[bool, str]:
    base_url = cfg.get("base_url", "").rstrip("/")
    app_id = cfg.get("app_id")
    username = cfg.get("username")
    password = cfg.get("password")
    if not (base_url and app_id and username and password):
        return False, "URL de base, App ID, utilisateur et mot de passe requis."
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(f"{base_url}/api/{app_id}/user/", auth=(username, password))
        data = resp.json()
        if data.get("success"):
            return True, "OK — authentification phpIPAM réussie"
        return False, f"Échec authentification : {data.get('message', resp.text[:200])}"
    except httpx.HTTPError as exc:
        return False, describe_connector_error(exc)


async def _test_glpi(cfg: dict) -> tuple[bool, str]:
    if not all(cfg.get(k) for k in ("base_url", "client_id", "client_secret", "username", "password")):
        return False, "URL de base, client OAuth2, utilisateur et mot de passe requis."
    try:
        # _get_token est synchrone (httpx.post) — passé dans un thread pour ne jamais
        # bloquer la boucle asyncio (sinon check_connectors() sérialise tout malgré le
        # asyncio.gather, un appel bloquant dans une coroutine bloque tout le monde).
        await asyncio.to_thread(_get_token, cfg)
        return True, "OK — authentification GLPI (OAuth2) réussie"
    except GlpiError as exc:
        return False, str(exc)
    except httpx.HTTPError as exc:
        return False, describe_connector_error(exc)


def _test_gitea(cfg: dict) -> tuple[bool, str]:
    ssh_url = cfg.get("ssh_url", "")
    if not ssh_url:
        return False, "URL SSH du repo requise."
    ssh_cmd = (
        f"ssh -i {settings.gitea_ssh_private_key_path} "
        "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8"
    )
    try:
        result = subprocess.run(
            ["git", "ls-remote", ssh_url],
            env={"GIT_SSH_COMMAND": ssh_cmd, "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=12,
        )
        if result.returncode == 0:
            return True, "OK — deploy key authentifiée, repo accessible"
        return False, f"git ls-remote a échoué : {result.stderr.strip()[:300]}"
    except subprocess.TimeoutExpired:
        return False, "Timeout — hôte injoignable."


async def _test_http_reachable(url: str, insecure: bool = False) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(verify=not insecure, timeout=8, follow_redirects=True) as client:
            resp = await client.get(url)
        return True, f"Hôte joignable — HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, describe_connector_error(exc)
