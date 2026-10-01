"""Journal d'audit — fichier dédié, persistant (volume Docker), séparé des logs
applicatifs bruts (stdout/docker logs, qui tournent et se perdent). Deux familles
d'événements : connexions (succès, échec, verrouillage, désactivation/réactivation de
compte) et requêtes HTTP (qui a fait quoi, quand). Format JSON-lines — une ligne par
événement, facile à grep/jq, pas besoin de parseur dédié."""
import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("/app/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("hephaestus.audit")
_logger.setLevel(logging.INFO)
if not _logger.handlers:  # évite les doublons si le module est importé plusieurs fois
    _handler = RotatingFileHandler(
        LOG_DIR / "audit.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.propagate = False  # ne duplique pas vers les logs docker (déjà capturés ailleurs)


def client_ip(request) -> str | None:
    """IP réelle du client — derrière le proxy Caddy, request.client.host ne donne que
    l'IP interne du conteneur proxy ; Caddy transmet la vraie IP via X-Forwarded-For
    (comportement par défaut de reverse_proxy), on la préfère quand elle est présente."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _write(event: str, **fields) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    _logger.info(json.dumps(entry, ensure_ascii=False))


def log_login_success(username: str, source: str, ip: str | None) -> None:
    _write("login_success", username=username, source=source, ip=ip)


def log_login_failure(username: str, reason: str, ip: str | None) -> None:
    _write("login_failure", username=username, reason=reason, ip=ip)


def log_logout(username: str, ip: str | None) -> None:
    _write("logout", username=username, ip=ip)


def log_account_locked(username: str) -> None:
    _write("account_locked_temporary", username=username)


def log_account_disabled(username: str) -> None:
    _write("account_disabled", username=username)


def log_account_reactivated(username: str, by: str | None) -> None:
    _write("account_reactivated", username=username, by=by)


def log_request(method: str, path: str, status: int, username: str | None, ip: str | None, duration_ms: int) -> None:
    _write("request", method=method, path=path, status=status, username=username, ip=ip, duration_ms=duration_ms)
