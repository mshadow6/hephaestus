"""Version courante de l'app + vérification de mise à jour contre les releases GitHub.

Vérification mise en cache (pas un appel réseau à chaque page) : GitHub serait interrogé
à chaque chargement sinon, inutilement lent et ça spam leur API pour rien.
"""
import logging
import time

import httpx

logger = logging.getLogger(__name__)

CURRENT_VERSION = "1.1.0"
REPO = "mshadow6/hephaestus"
CHECK_INTERVAL_SECONDS = 6 * 3600  # 6h - une app self-hosted n'a pas besoin de plus frais

_cache: dict = {"checked_at": 0.0, "latest": None}


def _parse_semver(version: str) -> tuple[int, int, int] | None:
    parts = version.lstrip("v").split(".")
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def check_update() -> dict:
    """Retourne {"update_available": bool, "latest": str|None, "current": str}.
    Jamais lève d'exception - une panne de cette vérification ne doit jamais empêcher
    l'app de fonctionner."""
    now = time.monotonic()
    if now - _cache["checked_at"] < CHECK_INTERVAL_SECONDS and _cache["checked_at"] > 0:
        latest = _cache["latest"]
    else:
        latest = None
        try:
            resp = httpx.get(
                f"https://api.github.com/repos/{REPO}/releases/latest", timeout=5,
            )
            if resp.status_code == 200:
                latest = resp.json().get("tag_name")
        except Exception as exc:  # noqa: BLE001 - jamais casser une page pour ça
            logger.warning("Vérification de mise à jour échouée : %s", exc)
        _cache["checked_at"] = now
        _cache["latest"] = latest

    current_parsed = _parse_semver(CURRENT_VERSION)
    latest_parsed = _parse_semver(latest) if latest else None
    update_available = bool(
        current_parsed and latest_parsed and latest_parsed > current_parsed
    )
    return {"update_available": update_available, "latest": latest, "current": CURRENT_VERSION}
