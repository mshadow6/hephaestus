from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit_log import log_account_reactivated
from app.auth import hash_password, require_admin
from app.counts import pending_approval_count
from app.database import get_db
from app.feature_flags import load_features, save_features
from app.keycloak_auth import load_keycloak_config, save_keycloak_config, test_keycloak_connection
from app.ldap_auth import load_ldap_config, save_ldap_config, test_ldap_connection
from app.models import User, UserRole
from app.templating import templates
from app.tls import TlsError, apply as tls_apply, cert_info, generate_self_signed, load_state, save_uploaded

router = APIRouter(prefix="/settings", include_in_schema=False, dependencies=[Depends(require_admin)])



@router.get("")
def settings_home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings.html",
        {"pending_count": pending_approval_count(db), "features": load_features()},
    )


@router.get("/help")
def help_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings_help.html", {"pending_count": pending_approval_count(db)}
    )


@router.post("/features")
def update_features(native_vm_form_enabled: str = Form(None)):
    save_features({"native_vm_form_enabled": native_vm_form_enabled is not None})
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/users")
def users_list(request: Request, db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.username)).all()
    return templates.TemplateResponse(
        request, "settings_users.html",
        {
            "users": users, "roles": UserRole, "pending_count": pending_approval_count(db),
            "error": None, "now": datetime.utcnow(),
        },
    )


@router.post("/users")
def users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("viewer"),
    db: Session = Depends(get_db),
):
    username = username.strip()
    role_enum = UserRole.admin if role == "admin" else UserRole.viewer

    def _error(message: str):
        users = db.scalars(select(User).order_by(User.username)).all()
        return templates.TemplateResponse(
            request, "settings_users.html",
            {"users": users, "roles": UserRole, "pending_count": pending_approval_count(db),
             "error": message, "now": datetime.utcnow()},
        )

    if len(password) < 8:
        return _error("Le mot de passe doit faire au moins 8 caractères.")
    if db.scalar(select(User).where(User.username == username)) is not None:
        return _error(f"L'utilisateur '{username}' existe déjà.")

    db.add(User(username=username, password_hash=hash_password(password), role=role_enum, source="local"))
    db.commit()
    return RedirectResponse(url="/settings/users", status_code=303)


@router.post("/users/{user_id}/unlock")
def users_unlock(user_id: int, request: Request, db: Session = Depends(get_db), admin: dict = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is not None:
        user.disabled = False
        user.locked_until = None
        user.failed_login_attempts = 0
        db.commit()
        log_account_reactivated(user.username, admin.get("username"))
    return RedirectResponse(url="/settings/users", status_code=303)


@router.post("/users/{user_id}/delete")
def users_delete(user_id: int, request: Request, db: Session = Depends(get_db)):
    current = request.session.get("user") or {}
    if current.get("id") == user_id:
        raise HTTPException(status_code=400, detail="Impossible de supprimer son propre compte")

    user = db.get(User, user_id)
    if user is not None:
        db.delete(user)
        db.commit()
    return RedirectResponse(url="/settings/users", status_code=303)


@router.get("/ldap")
def ldap_settings(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings_ldap.html",
        {"config": load_ldap_config(), "pending_count": pending_approval_count(db)},
    )


@router.post("/ldap")
def ldap_settings_save(
    request: Request,
    enabled: str = Form(None),
    server: str = Form(""),
    use_tls: str = Form(None),
    bind_dn: str = Form(""),
    bind_password: str = Form(""),
    search_base: str = Form(""),
    search_filter: str = Form("(uid={username})"),
    db: Session = Depends(get_db),
):
    config = {
        "enabled": enabled is not None,
        "server": server.strip(),
        "use_tls": use_tls is not None,
        "bind_dn": bind_dn.strip(),
        "bind_password": bind_password,
        "search_base": search_base.strip(),
        "search_filter": search_filter.strip() or "(uid={username})",
    }
    save_ldap_config(config)
    return RedirectResponse(url="/settings/ldap", status_code=303)


@router.post("/ldap/test")
def ldap_settings_test(
    request: Request,
    server: str = Form(""),
    use_tls: str = Form(None),
    bind_dn: str = Form(""),
    bind_password: str = Form(""),
    search_base: str = Form(""),
    search_filter: str = Form("(uid={username})"),
):
    config = {
        "server": server.strip(),
        "use_tls": use_tls is not None,
        "bind_dn": bind_dn.strip(),
        "bind_password": bind_password,
        "search_base": search_base.strip(),
        "search_filter": search_filter.strip() or "(uid={username})",
    }
    success, message = test_ldap_connection(config)
    return templates.TemplateResponse(
        request, "_test_result.html", {"success": success, "message": message}
    )


@router.get("/keycloak")
def keycloak_settings(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings_keycloak.html",
        {
            "config": load_keycloak_config(), "pending_count": pending_approval_count(db),
            "callback_url": str(request.url_for("keycloak_callback")),
        },
    )


@router.post("/keycloak")
def keycloak_settings_save(
    enabled: str = Form(None),
    issuer_url: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    default_role: str = Form("viewer"),
):
    config = {
        "enabled": enabled is not None,
        "issuer_url": issuer_url.strip(),
        "client_id": client_id.strip(),
        "client_secret": client_secret,
        "default_role": "admin" if default_role == "admin" else "viewer",
    }
    save_keycloak_config(config)
    return RedirectResponse(url="/settings/keycloak", status_code=303)


@router.post("/keycloak/test")
def keycloak_settings_test(
    request: Request,
    issuer_url: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
):
    config = {"issuer_url": issuer_url.strip(), "client_id": client_id.strip(), "client_secret": client_secret}
    success, message = test_keycloak_connection(config)
    return templates.TemplateResponse(
        request, "_test_result.html", {"success": success, "message": message}
    )


@router.get("/tls")
def tls_settings(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings_tls.html",
        {
            "pending_count": pending_approval_count(db),
            "state": load_state(), "cert": cert_info(),
            "error": request.query_params.get("error"),
            "saved": request.query_params.get("saved"),
        },
    )


@router.post("/tls/generate")
def tls_generate(common_name: str = Form(...)):
    common_name = common_name.strip()
    if not common_name:
        return RedirectResponse(url="/settings/tls?error=Domaine+ou+IP+requis", status_code=303)
    try:
        generate_self_signed(common_name)
        tls_apply(True)
    except TlsError as exc:
        return RedirectResponse(url=f"/settings/tls?error={exc}", status_code=303)
    return RedirectResponse(url="/settings/tls?saved=1", status_code=303)


@router.post("/tls/upload")
async def tls_upload(
    cert_file: UploadFile,
    key_file: UploadFile,
    chain_file: UploadFile | None = None,
):
    try:
        cert_pem = (await cert_file.read()).decode("utf-8", errors="replace")
        key_pem = (await key_file.read()).decode("utf-8", errors="replace")
        chain_pem = None
        if chain_file is not None and chain_file.filename:
            chain_pem = (await chain_file.read()).decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return RedirectResponse(
            url="/settings/tls?error=Fichier+illisible+(attendu+du+PEM+en+texte)", status_code=303
        )

    try:
        save_uploaded(cert_pem, key_pem, chain_pem)
        tls_apply(True)
    except TlsError as exc:
        return RedirectResponse(url=f"/settings/tls?error={exc}", status_code=303)
    return RedirectResponse(url="/settings/tls?saved=1", status_code=303)


@router.post("/tls/disable")
def tls_disable():
    try:
        tls_apply(False)
    except TlsError as exc:
        return RedirectResponse(url=f"/settings/tls?error={exc}", status_code=303)
    return RedirectResponse(url="/settings/tls?saved=1", status_code=303)


@router.post("/tls/reapply")
def tls_reapply():
    """Repousse l'état voulu (mémorisé) à Caddy — utile si le conteneur proxy a
    redémarré entre-temps et a rechargé le Caddyfile statique (HTTP) du dépôt."""
    try:
        tls_apply(load_state().get("enabled", False))
    except TlsError as exc:
        return RedirectResponse(url=f"/settings/tls?error={exc}", status_code=303)
    return RedirectResponse(url="/settings/tls?saved=1", status_code=303)
