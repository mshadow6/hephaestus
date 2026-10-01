from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import any_user_exists, get_user_by_username, hash_password, verify_password
from app.database import get_db
from app.ldap_auth import authenticate as ldap_authenticate
from app.models import User, UserRole
from app.templating import templates

router = APIRouter(include_in_schema=False)


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

    if user is not None and user.source == "local" and user.password_hash:
        if verify_password(password, user.password_hash):
            request.session["user"] = {"id": user.id, "username": user.username, "role": user.role.value}
            return RedirectResponse(url=next or "/", status_code=303)

    elif user is None and ldap_authenticate(username, password):
        # Premier login LDAP réussi : on crée un compte local "fantôme" (pas de mot de
        # passe stocké, l'auth repasse par l'annuaire à chaque fois) pour pouvoir gérer
        # son rôle depuis /settings/users. Rôle par défaut le plus restrictif.
        user = User(username=username, password_hash=None, role=UserRole.viewer, source="ldap")
        db.add(user)
        db.commit()
        request.session["user"] = {"id": user.id, "username": user.username, "role": user.role.value}
        return RedirectResponse(url=next or "/", status_code=303)

    elif user is not None and user.source == "ldap" and ldap_authenticate(username, password):
        request.session["user"] = {"id": user.id, "username": user.username, "role": user.role.value}
        return RedirectResponse(url=next or "/", status_code=303)

    return templates.TemplateResponse(
        request, "login.html",
        {"error": "Identifiant ou mot de passe incorrect.", "next": next},
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
