from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import any_user_exists, get_user_by_username, hash_password, verify_password_constant_time
from app.database import get_db
from app.ldap_auth import authenticate as ldap_authenticate
from app.models import User, UserRole
from app.templating import templates

router = APIRouter(include_in_schema=False)

LOCKOUT_THRESHOLD = 5          # échecs avant un verrouillage temporaire
LOCKOUT_DURATION = timedelta(minutes=1)
DISABLE_THRESHOLD = 10         # échecs avant verrouillage définitif (hors compte protégé)


@router.get("/setup")
def setup_form(request: Request, db: Session = Depends(get_db)):
    if any_user_exists(db):
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup")
def setup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    if any_user_exists(db):
        return RedirectResponse(url="/login")

    if len(password) < 8:
        return templates.TemplateResponse(
            request, "setup.html", {"error": "Le mot de passe doit faire au moins 8 caractères."}
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            request, "setup.html", {"error": "Les mots de passe ne correspondent pas."}
        )

    user = User(
        username=username, password_hash=hash_password(password),
        role=UserRole.admin, source="local",
        is_protected=True,  # tout premier admin = compte de secours, jamais verrouillable définitivement
    )
    db.add(user)
    db.commit()

    request.session["user"] = {"id": user.id, "username": user.username, "role": user.role.value}
    return RedirectResponse(url="/", status_code=303)


@router.get("/login")
def login_form(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if not any_user_exists(db):
        return RedirectResponse(url="/setup")
    return templates.TemplateResponse(request, "login.html", {"error": None, "next": next})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    user = get_user_by_username(db, username)
    now = datetime.utcnow()

    # Anti-bruteforce : s'applique aux comptes LOCAL et LDAP (le bind échoué compte pareil
    # — le risque de bruteforce est côté app, pas côté annuaire). Vérifié avant même de
    # tenter l'authentification, pour qu'un compte verrouillé ne consomme pas une tentative
    # LDAP en plus (pas de round-trip réseau inutile vers l'annuaire).
    if user is not None:
        if user.disabled:
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "Compte désactivé après trop d'échecs — contacte un administrateur.", "next": next},
                status_code=403,
            )
        if user.locked_until is not None and user.locked_until > now:
            wait_s = int((user.locked_until - now).total_seconds()) + 1
            return templates.TemplateResponse(
                request, "login.html",
                {"error": f"Trop de tentatives — réessaie dans {wait_s}s.", "next": next},
                status_code=429,
            )

    # bcrypt tourne systématiquement (hash factice si le compte local n'existe pas) pour
    # qu'un identifiant inconnu ne réponde pas sensiblement plus vite qu'un mauvais mot
    # de passe — sinon le temps de réponse permettrait d'énumérer les comptes valides.
    local_hash = user.password_hash if (user is not None and user.source == "local") else None
    local_password_ok = verify_password_constant_time(password, local_hash)

    auth_ok = False
    if user is not None and user.source == "local" and local_hash and local_password_ok:
        auth_ok = True
    elif user is None and ldap_authenticate(username, password):
        # Premier login LDAP réussi : on crée un compte local "fantôme" (pas de mot de
        # passe stocké, l'auth repasse par l'annuaire à chaque fois) pour pouvoir gérer
        # son rôle depuis /settings/users. Rôle par défaut le plus restrictif.
        user = User(username=username, password_hash=None, role=UserRole.viewer, source="ldap")
        db.add(user)
        db.commit()
        auth_ok = True
    elif user is not None and user.source == "ldap" and ldap_authenticate(username, password):
        auth_ok = True

    if auth_ok:
        user.failed_login_attempts = 0
        user.locked_until = None
        db.commit()
        request.session["user"] = {"id": user.id, "username": user.username, "role": user.role.value}
        return RedirectResponse(url=next or "/", status_code=303)

    if user is not None:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= DISABLE_THRESHOLD and not user.is_protected:
            user.disabled = True
            user.locked_until = None
        elif user.failed_login_attempts % LOCKOUT_THRESHOLD == 0:
            user.locked_until = now + LOCKOUT_DURATION
        db.commit()

    return templates.TemplateResponse(
        request, "login.html",
        {"error": "Identifiant ou mot de passe incorrect.", "next": next},
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
