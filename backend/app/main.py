import mimetypes
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler as fastapi_http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.audit_log import client_ip, log_request
from app.auth import NotAuthenticated
from app.config import settings
from app.routers import auth, connections, dashboard, playbooks, settings as settings_router, webhook
from app.templating import templates

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


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    # Le webhook GLPI est appelé par une machine (GLPI), pas un navigateur — il doit
    # garder une réponse JSON exploitable par son code, pas une page HTML. Toutes les
    # autres routes sont servies à un humain dans un navigateur : avant ce correctif,
    # une erreur (ex: 400/403/404) retournait du JSON brut sans aucun style — un retour
    # utilisateur explicite ("page noire") l'a signalé comme illisible.
    if request.url.path.startswith("/webhooks/"):
        return await fastapi_http_exception_handler(request, exc)
    # Une requête HTMX (hx-post/hx-get, ex: le bouton "Tester la connexion") attend un
    # petit fragment à insérer dans la page, pas une page HTML complète — sinon on se
    # retrouve avec un <html> entier collé dans un <div>, visuellement cassé.
    if request.headers.get("hx-request") == "true":
        return templates.TemplateResponse(
            request, "_error_fragment.html",
            {"status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )
    return templates.TemplateResponse(
        request, "error.html",
        {"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )


app.include_router(webhook.router)
app.include_router(auth.router)
app.include_router(settings_router.router)
app.include_router(connections.router)
app.include_router(playbooks.router)
app.include_router(dashboard.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
