from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import NotAuthenticated
from app.config import settings
from app.routers import auth, connections, dashboard, playbooks, settings as settings_router, webhook

app = FastAPI(title="Hephaestus API")

# max_age réduit (défaut Starlette : 14 jours) — une session admin qui traîne deux
# semaines sur un poste partagé est un risque inutile pour un outil qui peut déclencher
# de vraies créations/destructions de VM.
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, max_age=12 * 60 * 60)

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
