import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_login, require_permission
from app.connections.store import get_connection, list_connections
from app.counts import pending_approval_count
from app.database import get_db
from app.models import PlaybookRun, VMRequest
from app.playbooks.store import (
    CATEGORIES,
    CATEGORY_LABELS,
    InvalidPlaybookName,
    PlaybookDef,
    delete_playbook,
    get_playbook,
    list_playbooks,
    save_playbook,
)
from app.provisioning.inventory_file import (
    DISCOVERY_GROUPS,
    InvalidInventoryError,
    add_group,
    add_host,
    delete_group,
    delete_host,
    get_group_credential,
    list_structured,
    read_inventory,
    regenerate_discovery,
    set_group_credential,
    write_inventory,
)
from app.provisioning.inventory_file import list_groups as list_inventory_groups
from app.queue import vm_queue
from app.templating import templates
from app.worker.tasks import run_adhoc_playbook_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/playbooks", include_in_schema=False, dependencies=[Depends(require_login)])



@router.get("", dependencies=[Depends(require_permission("manage_playbooks"))])
def list_view(request: Request, db: Session = Depends(get_db), admin: dict = Depends(require_login)):
    playbooks = list_playbooks()
    return templates.TemplateResponse(
        request, "playbooks_list.html",
        {
            "playbooks": playbooks,
            "category_labels": CATEGORY_LABELS,
            "pending_count": pending_approval_count(db),
            "current_username": admin.get("username", ""),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/new", dependencies=[Depends(require_permission("manage_playbooks"))])
def new_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "playbooks_form.html",
        {
            "pb": None, "is_new": True, "categories": CATEGORIES,
            "category_labels": CATEGORY_LABELS, "pending_count": pending_approval_count(db),
        },
    )


@router.post("/new", dependencies=[Depends(require_permission("manage_playbooks"))])
async def create(request: Request):
    form = await request.form()
    return _save_from_form(form, existing_key=None)


@router.get("/{key}/edit", dependencies=[Depends(require_permission("manage_playbooks"))])
def edit_form(key: str, request: Request, db: Session = Depends(get_db)):
    pb = get_playbook(key)
    if pb is None:
        raise HTTPException(status_code=404, detail="Playbook introuvable")
    return templates.TemplateResponse(
        request, "playbooks_form.html",
        {
            "pb": pb, "is_new": False, "categories": CATEGORIES,
            "category_labels": CATEGORY_LABELS, "pending_count": pending_approval_count(db),
        },
    )


@router.post("/{key}/edit", dependencies=[Depends(require_permission("manage_playbooks"))])
async def update(key: str, request: Request):
    pb = get_playbook(key)
    if pb is None:
        raise HTTPException(status_code=404, detail="Playbook introuvable")
    form = await request.form()
    return _save_from_form(form, existing_key=key)


@router.post("/{key}/delete", dependencies=[Depends(require_permission("manage_playbooks"))])
def delete(key: str):
    delete_playbook(key)
    return RedirectResponse(url="/playbooks", status_code=303)


@router.post("/{key}/toggle-enabled", dependencies=[Depends(require_permission("manage_playbooks"))])
async def toggle_enabled(key: str, request: Request, admin: dict = Depends(require_login)):
    pb = get_playbook(key)
    if pb is None:
        raise HTTPException(status_code=404, detail="Playbook introuvable")

    form = await request.form()
    confirm_username = (form.get("confirm_username") or "").strip()
    if not confirm_username or confirm_username != admin.get("username", ""):
        return RedirectResponse(
            url="/playbooks?error=Nom+d%27utilisateur+incorrect+%E2%80%94+action+annul%C3%A9e",
            status_code=303,
        )

    pb.enabled = not pb.enabled
    save_playbook(pb)
    return RedirectResponse(url="/playbooks", status_code=303)


@router.get("/deploy", dependencies=[Depends(require_permission("run_playbooks"))])
def deploy_form(request: Request, db: Session = Depends(get_db)):
    groups = list_inventory_groups()
    playbooks = [p for p in list_playbooks() if p.enabled]
    return templates.TemplateResponse(
        request, "playbooks_deploy.html",
        {
            "groups": groups, "playbooks": playbooks,
            "pending_count": pending_approval_count(db),
        },
    )


@router.post("/deploy", dependencies=[Depends(require_permission("run_playbooks"))])
async def deploy(request: Request, db: Session = Depends(get_db), admin: dict = Depends(require_login)):
    form = await request.form()
    playbook_keys = form.getlist("playbook_keys")
    group = (form.get("group") or "").strip() or None
    manual_raw = (form.get("manual_targets") or "").strip()
    manual_targets = [line.strip() for line in manual_raw.splitlines() if line.strip()]
    dry_run = form.get("dry_run") is not None
    verbose = form.get("verbose") is not None

    if not playbook_keys:
        raise HTTPException(status_code=400, detail="Sélectionne au moins un playbook")
    if not group and not manual_targets:
        raise HTTPException(status_code=400, detail="Sélectionne un groupe de l'inventaire ou une cible manuelle")

    playbooks = [get_playbook(k) for k in playbook_keys]
    if any(p is None for p in playbooks):
        raise HTTPException(status_code=400, detail="Playbook inconnu")

    targets = {"group": group, "manual": manual_targets}

    run_ids = []
    for playbook in playbooks:
        run = PlaybookRun(
            playbook_key=playbook.key,
            playbook_label=playbook.label,
            target_hostnames=targets,
            launched_by=admin.get("username"),
            dry_run=dry_run,
            verbose=verbose,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_ids.append(run.id)
        vm_queue.enqueue(run_adhoc_playbook_job, run.id)

    if len(run_ids) == 1:
        return RedirectResponse(url=f"/playbooks/runs/{run_ids[0]}", status_code=303)
    return RedirectResponse(url="/playbooks/runs", status_code=303)


def _hypervisor_type(connection_name: str | None) -> str | None:
    if not connection_name:
        return None
    conn = get_connection(connection_name)
    return conn.type if conn else None


def _credential_connections():
    return [c for c in list_connections() if c.enabled and c.type == "ansible_credential"]


@router.get("/inventory", dependencies=[Depends(require_permission("run_playbooks"))])
def inventory_form(request: Request, db: Session = Depends(get_db)):
    all_groups = list_structured()
    discovery = {name: all_groups.pop(name, []) for name in DISCOVERY_GROUPS}

    recent_vms = db.scalars(
        select(VMRequest).order_by(VMRequest.created_at.desc()).limit(5)
    ).all()

    group_names = list(discovery.keys()) + list(all_groups.keys())
    host_counts = {name: len(discovery.get(name) or all_groups.get(name) or []) for name in group_names}
    credentials = {name: get_group_credential(name) for name in group_names}

    return templates.TemplateResponse(
        request, "playbooks_inventory.html",
        {
            "recent_vms": recent_vms,
            "hypervisor_type": _hypervisor_type,
            "discovery_names": list(discovery.keys()),
            "custom_names": list(all_groups.keys()),
            "host_counts": host_counts,
            "credentials": credentials,
            "pending_count": pending_approval_count(db),
            "error": request.query_params.get("error"),
            "saved": request.query_params.get("saved"),
        },
    )


@router.get("/inventory/yaml-fragment", dependencies=[Depends(require_permission("run_playbooks"))])
def inventory_yaml_fragment(request: Request):
    return templates.TemplateResponse(
        request, "_inventory_yaml_fragment.html",
        {"content": read_inventory(), "error": request.query_params.get("error")},
    )


@router.get("/inventory/groups/{group}/edit-fragment", dependencies=[Depends(require_permission("run_playbooks"))])
def inventory_group_edit_fragment(group: str, request: Request):
    hosts = list_structured().get(group, [])
    return templates.TemplateResponse(
        request, "_inventory_group_edit_fragment.html",
        {
            "group_name": group,
            "hosts": hosts,
            "credentials": _credential_connections(),
            "current_credential": get_group_credential(group),
            "is_discovery": group in DISCOVERY_GROUPS,
        },
    )


@router.post("/inventory/groups/{group}/credential", dependencies=[Depends(require_permission("run_playbooks"))])
async def inventory_group_credential(group: str, request: Request):
    form = await request.form()
    set_group_credential(group, (form.get("credential") or "").strip() or None)
    return RedirectResponse(url="/playbooks/inventory?saved=1", status_code=303)


@router.post("/inventory", dependencies=[Depends(require_permission("run_playbooks"))])
async def inventory_save(request: Request):
    form = await request.form()
    content = form.get("content", "")
    try:
        write_inventory(content)
    except InvalidInventoryError as exc:
        return RedirectResponse(url=f"/playbooks/inventory?error={exc}", status_code=303)
    return RedirectResponse(url="/playbooks/inventory?saved=1", status_code=303)


@router.post("/inventory/regenerate", dependencies=[Depends(require_permission("run_playbooks"))])
def inventory_regenerate():
    try:
        regenerate_discovery()
    except Exception:  # noqa: BLE001 — jamais casser la page pour un souci de découverte
        logger.exception("Régénération de l'inventaire échouée")
        return RedirectResponse(
            url="/playbooks/inventory?error=Découverte+Proxmox+échouée+(voir+logs)", status_code=303
        )
    return RedirectResponse(url="/playbooks/inventory?saved=1", status_code=303)


@router.post("/inventory/groups", dependencies=[Depends(require_permission("run_playbooks"))])
async def inventory_group_add(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return RedirectResponse(url="/playbooks/inventory?error=Nom+de+groupe+requis", status_code=303)
    add_group(name)
    return RedirectResponse(url="/playbooks/inventory?saved=1", status_code=303)


@router.post("/inventory/groups/{group}/delete", dependencies=[Depends(require_permission("run_playbooks"))])
def inventory_group_delete(group: str):
    delete_group(group)
    return RedirectResponse(url="/playbooks/inventory?saved=1", status_code=303)


@router.post("/inventory/groups/{group}/hosts", dependencies=[Depends(require_permission("run_playbooks"))])
async def inventory_host_add(group: str, request: Request):
    form = await request.form()
    hostname = (form.get("hostname") or "").strip()
    ansible_host = (form.get("ansible_host") or "").strip()
    if not hostname:
        return RedirectResponse(url="/playbooks/inventory?error=Nom+d%27hôte+requis", status_code=303)
    add_host(group, hostname, ansible_host)
    return RedirectResponse(url="/playbooks/inventory?saved=1", status_code=303)


@router.post("/inventory/groups/{group}/hosts/{hostname}/delete", dependencies=[Depends(require_permission("run_playbooks"))])
def inventory_host_delete(group: str, hostname: str):
    delete_host(group, hostname)
    return RedirectResponse(url="/playbooks/inventory?saved=1", status_code=303)


@router.get("/runs")
def runs_list(request: Request, db: Session = Depends(get_db), show_all: bool = False, playbook: str = ""):
    base = select(PlaybookRun)
    if playbook:
        base = base.where(PlaybookRun.playbook_key == playbook)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    query = base.order_by(PlaybookRun.created_at.desc())
    if not show_all:
        query = query.limit(10)
    runs = db.scalars(query).all()
    playbook_def = get_playbook(playbook) if playbook else None
    return templates.TemplateResponse(
        request, "playbook_runs_list.html",
        {
            "runs": runs, "show_all": show_all, "total": total,
            "playbook_filter": playbook, "playbook_def": playbook_def,
            "pending_count": pending_approval_count(db),
        },
    )


@router.get("/runs/{run_id}")
def run_detail(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(PlaybookRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Exécution introuvable")
    return templates.TemplateResponse(
        request, "playbook_run_detail.html",
        {"run": run, "pending_count": pending_approval_count(db)},
    )


@router.get("/runs/{run_id}/status-fragment")
def run_status_fragment(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(PlaybookRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Exécution introuvable")
    return templates.TemplateResponse(request, "_playbook_run_status_fragment.html", {"run": run})


def _save_from_form(form, existing_key: str | None):
    key = (form.get("key") or "").strip() if existing_key is None else existing_key
    label = (form.get("label") or "").strip()
    repo_path = (form.get("repo_path") or "").strip()
    category = form.get("category") or "optional"
    description = (form.get("description") or "").strip()
    enabled = form.get("enabled") is not None
    try:
        order = int(form.get("order") or 0)
    except ValueError:
        order = 0

    if not label or not repo_path:
        raise HTTPException(status_code=400, detail="Nom et chemin dans le repo requis")
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Catégorie inconnue")

    try:
        save_playbook(PlaybookDef(
            key=key, label=label, repo_path=repo_path, category=category,
            description=description, enabled=enabled, order=order,
        ))
    except InvalidPlaybookName as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url="/playbooks", status_code=303)
