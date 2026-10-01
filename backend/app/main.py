import mimetypes
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.audit_log import client_ip, log_request
from app.auth import NotAuthenticated
from app.config import settings
from app.routers import auth, connections, dashboard, playbooks, settings as settings_router, webhook

app = FastAPI(title="Hephaestus API")

# L'image Python slim n'a pas .woff2 dans sa base mimetypes système -> StaticFiles le
# servait en text/plain, que certains navigateurs refusent de charger comme police par
# sécurité (vérifié : le fichier répondait 200 mais avec le mauvais Content-Type).
mimetypes.add_type("font/woff2", ".woff2")

# max_age réduit (défaut Starlette : 14 jours) — une session admin qui traîne deux
# semaines sur un poste partagé est un risque inutile pour un outil qui peut déclencher
# de vraies créations/destructions de VM.
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, max_age=12 * 60 * 60)

_SKIP_AUDIT_PREFIXES = ("/static/",)
_SKIP_AUDIT_PATHS = {"/health"}


@app.middleware("http")
async def audit_requests(request: Request, call_next):
    """Journalise chaque requête (fichier dédié, voir app.audit_log) — exclut les
    fichiers statiques et le healthcheck, bruit sans intérêt d'audit."""
    start = time.monotonic()
    response = await call_next(request)
    path = request.url.path
    if path in _SKIP_AUDIT_PATHS or path.startswith(_SKIP_AUDIT_PREFIXES):
        return response
    user = (request.session.get("user") or {}).get("username") if hasattr(request, "session") else None
    log_request(
        method=request.method, path=path, status=response.status_code,
        username=user, ip=client_ip(request),
        duration_ms=int((time.monotonic() - start) * 1000),
    )
    return response

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url=f"/login?next={request.url.path}")


app.include_router(webhook.router)
app.include_router(auth.router)
app.include_router(settings_router.router)
app.include_router(connections.router)
app.include_router(playbooks.router)
app.include_router(dashboard.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
