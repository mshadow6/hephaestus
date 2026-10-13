from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit_log import log_account_reactivated
from app.auth import hash_password, require_login, require_permission, verify_password_constant_time
from app.counts import pending_approval_count
from app.database import get_db
from app.feature_flags import load_features, save_features
from app.keycloak_auth import load_keycloak_config, save_keycloak_config, test_keycloak_connection
from app.ldap_auth import load_ldap_config, save_ldap_config, test_ldap_connection
from app.models import Group, User
from app.permissions import PERMISSIONS
from app.templating import templates
from app.tls import TlsError, apply as tls_apply, cert_info, generate_self_signed, load_state, save_uploaded
from app.version import check_update

router = APIRouter(prefix="/settings", include_in_schema=False, dependencies=[Depends(require_login)])



@router.get("", dependencies=[Depends(require_permission("manage_settings"))])
def settings_home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings.html",
        {"pending_count": pending_approval_count(db), "features": load_features()},
    )


@router.get("/update-fragment", dependencies=[Depends(require_permission("manage_settings"))])
def update_fragment(request: Request):
    return templates.TemplateResponse(
        request, "_update_status_card.html", check_update(),
    )


@router.get("/help")
def help_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings_help.html", {"pending_count": pending_approval_count(db)}
    )


@router.get("/password")
def password_form(request: Request, db: Session = Depends(get_db), user: dict = Depends(require_login)):
    me = db.get(User, user["id"])
    return templates.TemplateResponse(
        request, "settings_password.html",
        {"pending_count": pending_approval_count(db), "is_local": me.source == "local",
         "error": None, "saved": request.query_params.get("saved")},
    )


@router.post("/password")
def password_update(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_login),
):
    me = db.get(User, user["id"])

    def _error(message: str):
        return templates.TemplateResponse(
            request, "settings_password.html",
            {"pending_count": pending_approval_count(db), "is_local": me.source == "local",
             "error": message, "saved": None},
            status_code=400,
        )

    if me.source != "local":
        return _error("Ce compte est géré en externe (LDAP/Keycloak) — le mot de passe ne se change pas ici.")
    if not verify_password_constant_time(current_password, me.password_hash):
        return _error("Mot de passe actuel incorrect.")
    if len(new_password) < 8:
        return _error("Le nouveau mot de passe doit faire au moins 8 caractères.")
    if new_password != new_password_confirm:
        return _error("Les deux mots de passe saisis ne correspondent pas.")

    me.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse(url="/settings/password?saved=1", status_code=303)


@router.post("/features", dependencies=[Depends(require_permission("manage_settings"))])
def update_features(native_vm_form_enabled: str = Form(None)):
    save_features({"native_vm_form_enabled": native_vm_form_enabled is not None})
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/users", dependencies=[Depends(require_permission("manage_users"))])
def users_list(request: Request, db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.username)).all()
    groups = db.scalars(select(Group).order_by(Group.name)).all()
    return templates.TemplateResponse(
        request, "settings_users.html",
        {
            "users": users, "groups": groups, "pending_count": pending_approval_count(db),
            "error": None, "now": datetime.utcnow(),
        },
    )


@router.post("/users", dependencies=[Depends(require_permission("manage_users"))])
def users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    group_id: int = Form(...),
    db: Session = Depends(get_db),
):
    username = username.strip()
    group = db.get(Group, group_id)

    def _error(message: str):
        users = db.scalars(select(User).order_by(User.username)).all()
        groups = db.scalars(select(Group).order_by(Group.name)).all()
        return templates.TemplateResponse(
            request, "settings_users.html",
            {"users": users, "groups": groups, "pending_count": pending_approval_count(db),
             "error": message, "now": datetime.utcnow()},
        )

    if len(password) < 8:
        return _error("Le mot de passe doit faire au moins 8 caractères.")
    if group is None:
        return _error("Groupe inconnu.")
    if db.scalar(select(User).where(User.username == username)) is not None:
        return _error(f"L'utilisateur '{username}' existe déjà.")

    db.add(User(username=username, password_hash=hash_password(password), group=group, source="local"))
    db.commit()
    return RedirectResponse(url="/settings/users", status_code=303)


@router.post("/users/{user_id}/unlock", dependencies=[Depends(require_permission("manage_users"))])
def users_unlock(user_id: int, request: Request, db: Session = Depends(get_db), admin: dict = Depends(require_login)):
    user = db.get(User, user_id)
    if user is not None:
        user.disabled = False
        user.locked_until = None
        user.failed_login_attempts = 0
        db.commit()
        log_account_reactivated(user.username, admin.get("username"))
    return RedirectResponse(url="/settings/users", status_code=303)


@router.post("/users/{user_id}/delete", dependencies=[Depends(require_permission("manage_users"))])
def users_delete(user_id: int, request: Request, db: Session = Depends(get_db)):
    current = request.session.get("user") or {}
    if current.get("id") == user_id:
        raise HTTPException(status_code=400, detail="Impossible de supprimer son propre compte")

    user = db.get(User, user_id)
    if user is not None:
        db.delete(user)
        db.commit()
    return RedirectResponse(url="/settings/users", status_code=303)


@router.get("/ldap", dependencies=[Depends(require_permission("manage_settings"))])
def ldap_settings(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings_ldap.html",
        {"config": load_ldap_config(), "pending_count": pending_approval_count(db)},
    )


@router.post("/ldap", dependencies=[Depends(require_permission("manage_settings"))])
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
    # Champ mot de passe vide à l'édition -> on garde l'ancien plutôt que d'écraser avec
    # une chaîne vide (même convention que les connexions, voir app/connections/store.py).
    if not bind_password:
        bind_password = load_ldap_config().get("bind_password", "")
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


@router.post("/ldap/test", dependencies=[Depends(require_permission("manage_settings"))])
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


@router.get("/keycloak", dependencies=[Depends(require_permission("manage_settings"))])
def keycloak_settings(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings_keycloak.html",
        {
            "config": load_keycloak_config(), "pending_count": pending_approval_count(db),
            "callback_url": str(request.url_for("keycloak_callback")),
            "groups": db.scalars(select(Group).order_by(Group.name)).all(),
        },
    )


@router.post("/keycloak", dependencies=[Depends(require_permission("manage_settings"))])
def keycloak_settings_save(
    db: Session = Depends(get_db),
    enabled: str = Form(None),
    issuer_url: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    default_group: str = Form("Invité"),
):
    if not client_secret:
        client_secret = load_keycloak_config().get("client_secret", "")
    valid_group = db.scalar(select(Group).where(Group.name == default_group)) is not None
    config = {
        "enabled": enabled is not None,
        "issuer_url": issuer_url.strip(),
        "client_id": client_id.strip(),
        "client_secret": client_secret,
        "default_group": default_group if valid_group else "Invité",
    }
    save_keycloak_config(config)
    return RedirectResponse(url="/settings/keycloak", status_code=303)


@router.post("/keycloak/test", dependencies=[Depends(require_permission("manage_settings"))])
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


@router.get("/tls", dependencies=[Depends(require_permission("manage_settings"))])
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


@router.post("/tls/generate", dependencies=[Depends(require_permission("manage_settings"))])
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


@router.post("/tls/upload", dependencies=[Depends(require_permission("manage_settings"))])
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


@router.post("/tls/disable", dependencies=[Depends(require_permission("manage_settings"))])
def tls_disable():
    try:
        tls_apply(False)
    except TlsError as exc:
        return RedirectResponse(url=f"/settings/tls?error={exc}", status_code=303)
    return RedirectResponse(url="/settings/tls?saved=1", status_code=303)


@router.post("/tls/reapply", dependencies=[Depends(require_permission("manage_settings"))])
def tls_reapply():
    """Repousse l'état voulu (mémorisé) à Caddy — utile si le conteneur proxy a
    redémarré entre-temps et a rechargé le Caddyfile statique (HTTP) du dépôt."""
    try:
        tls_apply(load_state().get("enabled", False))
    except TlsError as exc:
        return RedirectResponse(url=f"/settings/tls?error={exc}", status_code=303)
    return RedirectResponse(url="/settings/tls?saved=1", status_code=303)


@router.get("/groups", dependencies=[Depends(require_permission("manage_groups"))])
def groups_list(request: Request, db: Session = Depends(get_db)):
    groups = db.scalars(select(Group).order_by(Group.is_system.desc(), Group.name)).all()
    counts = {g.id: db.scalar(select(func.count()).select_from(User).where(User.group_id == g.id)) for g in groups}
    return templates.TemplateResponse(
        request, "settings_groups.html",
        {"groups": groups, "user_counts": counts, "pending_count": pending_approval_count(db), "error": None},
    )


@router.get("/groups/new", dependencies=[Depends(require_permission("manage_groups"))])
def group_new_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "settings_group_form.html",
        {"group": None, "all_permissions": PERMISSIONS, "pending_count": pending_approval_count(db), "error": None},
    )


@router.post("/groups/new", dependencies=[Depends(require_permission("manage_groups"))])
def group_create(
    request: Request,
    name: str = Form(...),
    permissions: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    name = name.strip()

    def _error(message: str):
        return templates.TemplateResponse(
            request, "settings_group_form.html",
            {"group": None, "all_permissions": PERMISSIONS, "pending_count": pending_approval_count(db),
             "error": message},
            status_code=400,
        )

    if not name:
        return _error("Nom de groupe requis.")
    if db.scalar(select(Group).where(Group.name == name)) is not None:
        return _error(f"Le groupe '{name}' existe déjà.")

    valid = [p for p in permissions if p in PERMISSIONS]
    db.add(Group(name=name, permissions=valid, is_system=False))
    db.commit()
    return RedirectResponse(url="/settings/groups", status_code=303)


@router.get("/groups/{group_id}/edit", dependencies=[Depends(require_permission("manage_groups"))])
def group_edit_form(group_id: int, request: Request, db: Session = Depends(get_db)):
    group = db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Groupe introuvable")
    return templates.TemplateResponse(
        request, "settings_group_form.html",
        {"group": group, "all_permissions": PERMISSIONS, "pending_count": pending_approval_count(db), "error": None},
    )


@router.post("/groups/{group_id}/edit", dependencies=[Depends(require_permission("manage_groups"))])
def group_update(
    group_id: int,
    request: Request,
    name: str = Form(...),
    permissions: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    group = db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Groupe introuvable")

    def _error(message: str):
        return templates.TemplateResponse(
            request, "settings_group_form.html",
            {"group": group, "all_permissions": PERMISSIONS, "pending_count": pending_approval_count(db),
             "error": message},
            status_code=400,
        )

    if group.is_system:
        # Admin/Invité : toujours gardés tels quels, sinon risque de se retrouver sans
        # aucun groupe ayant toutes les permissions (plus personne ne peut rien gérer).
        return _error(f"Le groupe '{group.name}' est un groupe système — ni le nom ni ses permissions ne se modifient.")

    name = name.strip()
    if not name:
        return _error("Nom de groupe requis.")
    if name != group.name and db.scalar(select(Group).where(Group.name == name)) is not None:
        return _error(f"Le groupe '{name}' existe déjà.")

    group.name = name
    group.permissions = [p for p in permissions if p in PERMISSIONS]
    db.commit()
    return RedirectResponse(url="/settings/groups", status_code=303)


@router.post("/groups/{group_id}/delete", dependencies=[Depends(require_permission("manage_groups"))])
def group_delete(group_id: int, db: Session = Depends(get_db)):
    group = db.get(Group, group_id)
    if group is None:
        return RedirectResponse(url="/settings/groups", status_code=303)
    if group.is_system:
        return RedirectResponse(url="/settings/groups?error=Groupe+système+non+supprimable", status_code=303)
    if db.scalar(select(func.count()).select_from(User).where(User.group_id == group_id)):
        return RedirectResponse(
            url="/settings/groups?error=Déplace+d%27abord+les+utilisateurs+de+ce+groupe", status_code=303
        )
    db.delete(group)
    db.commit()
    return RedirectResponse(url="/settings/groups", status_code=303)
