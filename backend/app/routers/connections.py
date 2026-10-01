from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.connections.store import (
    Connection,
    InvalidConnectionName,
    delete_connection,
    get_connection,
    list_connections,
    save_connection,
)
from app.connections.testing import test_connection
from app.connections.types import CATEGORY_LABELS, PROVIDER_TYPES, categories_in_use, provider_types_for_category
from app.counts import pending_approval_count
from app.database import get_db
from app.templating import templates

router = APIRouter(prefix="/connections", include_in_schema=False, dependencies=[Depends(require_admin)])



@router.get("")
def list_view(request: Request, db: Session = Depends(get_db)):
    connections = list_connections()
    return templates.TemplateResponse(
        request, "connections_list.html",
        {"connections": connections, "provider_types": PROVIDER_TYPES,
         "category_labels": CATEGORY_LABELS, "pending_count": pending_approval_count(db)},
    )


@router.get("/new")
def new_form(request: Request, category: str = "", type: str = "", db: Session = Depends(get_db)):
    # Étape 1 : pas encore de catégorie choisie -> sélecteur de catégorie (Hyperviseur,
    # IPAM, DNS, ...) plutôt qu'une seule liste plate de tous les types mélangés.
    if not category:
        return templates.TemplateResponse(
            request, "connections_new_category.html",
            {"categories": categories_in_use(), "pending_count": pending_approval_count(db)},
        )

    types_in_category = provider_types_for_category(category)
    if not types_in_category:
        raise HTTPException(status_code=404, detail="Catégorie inconnue")
    selected_type = type if type in types_in_category else next(iter(types_in_category))

    return templates.TemplateResponse(
        request, "connections_form.html",
        {
            "provider_types": types_in_category,
            "category": category,
            "category_label": CATEGORY_LABELS.get(category, category),
            "selected_type": selected_type,
            "conn": None,
            "is_new": True,
            "pending_count": pending_approval_count(db),
        },
    )


@router.get("/new/fields")
def new_fields(request: Request, category: str = "", type: str = ""):
    types_in_category = provider_types_for_category(category) or PROVIDER_TYPES
    selected_type = type if type in types_in_category else next(iter(types_in_category))
    return templates.TemplateResponse(
        request, "_connection_fields.html",
        {"provider_types": types_in_category, "selected_type": selected_type, "conn": None},
    )


@router.post("/new")
async def create(request: Request):
    form = await request.form()
    return _save_from_form(form, existing=None)


@router.get("/{name}/edit")
def edit_form(name: str, request: Request, db: Session = Depends(get_db)):
    conn = get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connexion introuvable")
    return templates.TemplateResponse(
        request, "connections_form.html",
        {
            "provider_types": PROVIDER_TYPES,
            "selected_type": conn.type,
            "conn": conn,
            "is_new": False,
            "pending_count": pending_approval_count(db),
        },
    )


@router.post("/{name}/edit")
async def update(name: str, request: Request):
    conn = get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connexion introuvable")
    form = await request.form()
    return _save_from_form(form, existing=conn)


@router.post("/{name}/delete")
def delete(name: str):
    delete_connection(name)
    return RedirectResponse(url="/connections", status_code=303)


@router.post("/{name}/toggle-enabled")
def toggle_enabled(name: str):
    conn = get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connexion introuvable")
    conn.enabled = not conn.enabled
    if not conn.enabled:
        conn.active = False  # une connexion désactivée ne peut pas rester "active"
    save_connection(conn)
    return RedirectResponse(url="/connections", status_code=303)


@router.post("/{name}/toggle-active")
def toggle_active(name: str):
    conn = get_connection(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connexion introuvable")
    conn.active = not conn.active
    if conn.active:
        conn.enabled = True  # une connexion active doit forcément être activée
    save_connection(conn)
    return RedirectResponse(url="/connections", status_code=303)


@router.post("/test")
async def test(request: Request):
    form = await request.form()
    provider_type = form.get("type", "")
    fields = PROVIDER_TYPES.get(provider_type)
    # Si on teste une connexion existante (formulaire d'édition), on récupère ses secrets
    # déjà enregistrés pour les champs mot de passe laissés vides — jamais renvoyés en
    # clair par le formulaire lui-même (voir _connection_fields.html).
    existing_name = form.get("name", "").strip()
    existing = get_connection(existing_name) if existing_name else None

    config = {}
    if fields:
        for f in fields.fields:
            if f.input_type == "checkbox":
                config[f.name] = form.get(f.name) is not None
            elif f.input_type == "password":
                submitted = form.get(f.name, "")
                config[f.name] = submitted if submitted else (existing.config.get(f.name, "") if existing else "")
            else:
                config[f.name] = form.get(f.name, "")

    conn = Connection(name="__test__", type=provider_type, config=config)
    success, message = await test_connection(conn)
    return templates.TemplateResponse(
        request, "_test_result.html", {"success": success, "message": message}
    )


def _save_from_form(form, existing: Connection | None):
    name = (form.get("name") or "").strip() if existing is None else existing.name
    provider_type = form.get("type", "")
    provider = PROVIDER_TYPES.get(provider_type)
    if provider is None:
        raise HTTPException(status_code=400, detail="Type de provider inconnu")

    config = {}
    for f in provider.fields:
        if f.input_type == "checkbox":
            config[f.name] = form.get(f.name) is not None
        elif f.input_type == "password":
            submitted = form.get(f.name, "")
            # Champ mot de passe laissé vide à l'édition -> on garde l'ancienne valeur
            # (jamais pré-remplie dans le formulaire, voir _connection_fields.html).
            config[f.name] = submitted if submitted else (existing.config.get(f.name, "") if existing else "")
        else:
            config[f.name] = form.get(f.name, "")

    enabled = form.get("enabled") is not None
    active = form.get("active") is not None

    try:
        save_connection(
            Connection(name=name, type=provider_type, enabled=enabled, active=active, config=config)
        )
    except InvalidConnectionName as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url="/connections", status_code=303)
