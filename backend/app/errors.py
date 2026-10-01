"""Transforme une exception technique de connecteur (souvent cryptique — "timed out",
"401"...) en message explicite : quel hôte/port a posé problème, timeout ou refus
d'authentification, plutôt que de laisser l'utilisateur aller fouiller les logs du worker
à chaque panne réseau (retour direct user : "ça c'est super cool d'avoir du code comme
ça")."""
import httpx


def describe_connector_error(exc: Exception) -> str:
    request = getattr(exc, "request", None)
    host_port = None
    if request is not None:
        url = request.url
        host_port = f"{url.host}:{url.port}" if url.port else str(url.host)
    where = f" à {host_port}" if host_port else ""

    if isinstance(exc, httpx.ConnectTimeout):
        return f"Connexion impossible{where} — hôte injoignable ou port fermé (timeout de connexion)."
    if isinstance(exc, httpx.ConnectError):
        return f"Connexion refusée{where} — hôte injoignable, DNS invalide, ou service arrêté."
    if isinstance(exc, httpx.ReadTimeout):
        return f"Pas de réponse{where} à temps (timeout de lecture) — service surchargé ou bloqué."
    if isinstance(exc, httpx.TimeoutException):
        return f"Délai d'attente dépassé{where}."
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return f"Authentification refusée (HTTP {status}){where} — identifiants invalides ou droits insuffisants."
        return f"Erreur HTTP {status}{where} : {exc.response.text[:200]}"
    if isinstance(exc, httpx.TransportError):
        return f"Erreur réseau{where} : {exc}"
    return str(exc)
