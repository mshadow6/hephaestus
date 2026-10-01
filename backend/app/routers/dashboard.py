import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_admin, require_login
from app.connections.store import get_active_connection
from app.counts import pending_approval_count
from app.database import get_db
from app.glpi.client import GlpiError, add_change_followup
from app.health import check_connectors, check_postgres, check_redis, check_worker
from app.models import PlaybookRun, RequestStatus, VMRequest
from app.playbooks.store import list_playbooks
from app.provisioning.ansible_runner import PLAYBOOKS_DIR
from app.queue import vm_queue
from app.templating import templates
from app.worker.tasks import process_vm_request

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False, dependencies=[Depends(require_login)])


def _glpi_base_url() -> str:
    conn = get_active_connection("itsm")
    return conn.config.get("base_url", "").rstrip("/") if conn else ""


def _notify_glpi(vm_request: VMRequest, content: str) -> None:
    """Poste un commentaire sur le ticket GLPI d'origine, best-effort — ne doit jamais
    faire échouer une approbation/rejet à cause d'un souci côté GLPI (hôte injoignable,
    connexion mal configurée, etc.), juste logger."""
    if not vm_request.glpi_ticket_id:
        return
    conn = get_active_connection("itsm")
    if conn is None:
        logger.info("Pas de connexion GLPI active — notification ignorée pour %s", vm_request.id)
        return
    try:
        add_change_followup(conn.config, vm_request.glpi_ticket_id, content)
    except GlpiError as exc:
        logger.warning(
            "Notification GLPI échouée pour la requête %s (ticket %s) : %s",
            vm_request.id, vm_request.glpi_ticket_id, exc,
        )


def _recent_activity(db: Session, limit: int = 8) -> list[dict]:
    """Fusionne les demandes de VM et les exécutions de playbooks en un seul flux
    chronologique — pas un vrai journal d'événements par transition (juste l'état actuel
    de chaque ligne, à sa dernière mise à jour), suffisant pour un aperçu d'activité."""
    vm_events = [
        {
            "timestamp": r.updated_at,
            "kind": "vm",
            "title": r.hostname,
            "status": r.status.value,
            "url": f"/vm/{r.id}",
        }
        for r in db.scalars(select(VMRequest).order_by(VMRequest.updated_at.desc()).limit(limit)).all()
    ]
    run_events = [
        {
            "timestamp": run.finished_at or run.created_at,
            "kind": "playbook",
            "title": run.playbook_label,
            "status": run.status.value,
            "url": f"/playbooks/runs/{run.id}",
        }
        for run in db.scalars(select(PlaybookRun).order_by(PlaybookRun.created_at.desc()).limit(limit)).all()
    ]
    merged = sorted(vm_events + run_events, key=lambda e: e["timestamp"], reverse=True)
    return merged[:limit]


@router.get("/")
async def list_requests(request: Request, db: Session = Depends(get_db)):
    counts_query = db.execute(
        select(VMRequest.status, func.count()).group_by(VMRequest.status)
    ).all()
    status_counts = {status.value: count for status, count in counts_query}
    pending_count = status_counts.get(RequestStatus.pending_approval.value, 0)

    return templates.TemplateResponse(
        request, "list.html",
        {
            "status_counts": status_counts,
            "pending_count": pending_count,
            "recent_activity": _recent_activity(db),
        },
    )


@router.get("/requests/new")
def new_request_form(request: Request, db: Session = Depends(get_db)):
    """Formulaire natif de demande de VM — second point d'entrée possible en plus du
    webhook GLPI (/webhooks/glpi), tous les deux créent le même VMRequest et suivent
    ensuite exactement le même pipeline (validation -> IPAM -> Terraform -> Ansible).
    GLPI n'est qu'une source de demandes parmi d'autres possibles, pas une dépendance du
    pipeline lui-même — voir VMRequest.glpi_ticket_id (nullable) et _notify_glpi (no-op
    silencieux si pas de ticket associé)."""
    return templates.TemplateResponse(
        request, "requests_new.html",
        {"pending_count": pending_approval_count(db), "error": request.query_params.get("error")},
    )


@router.post("/requests/new")
async def create_request(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    hostname = (form.get("hostname") or "").strip()
    if not hostname:
        return RedirectResponse(url="/requests/new?error=Nom+d%27hôte+requis", status_code=303)

    def _int_or_none(key: str) -> int | None:
        raw = (form.get(key) or "").strip()
        return int(raw) if raw else None

    user = request.session.get("user") or {}
    vm_request = VMRequest(
        hostname=hostname,
        glpi_ticket_id=None,
        requested_by=user.get("username"),
        environment=(form.get("environment") or "").strip() or None,
        vlan=(form.get("vlan") or "").strip() or None,
        cpu=_int_or_none("cpu"),
        ram_gb=_int_or_none("ram_gb"),
        disk_gb=_int_or_none("disk_gb"),
        os_template=(form.get("os_template") or "").strip() or None,
        raw_payload={"source": "native_form", "submitted_by": user.get("username")},
        status=RequestStatus.pending_approval,
    )
    db.add(vm_request)
    db.commit()
    db.refresh(vm_request)
    return RedirectResponse(url=f"/vm/{vm_request.id}", status_code=303)


@router.get("/deployments")
def deployments(
    request: Request, db: Session = Depends(get_db),
    status: str = "", q: str = "",
):
    """Historique complet des déploiements de VM, filtrable par statut et par nom
    d'hôte — page dédiée, distincte de l'aperçu (page d'accueil) et de la file de
    validation (/validations)."""
    query = select(VMRequest).order_by(VMRequest.created_at.desc())
    if status:
        query = query.where(VMRequest.status == status)
    if q:
        query = query.where(VMRequest.hostname.ilike(f"%{q}%"))
    requests = db.scalars(query).all()

    return templates.TemplateResponse(
        request, "deployments.html",
        {
            "requests": requests,
            "status_filter": status,
            "q": q,
            "all_statuses": [s.value for s in RequestStatus],
            "glpi_base_url": _glpi_base_url(),
            "pending_count": pending_approval_count(db),
        },
    )


@router.get("/health-fragment")
async def health_fragment(request: Request, db: Session = Depends(get_db)):
    """Chargé en différé (hx-get) par la page d'accueil : les tests de connecteurs
    (Proxmox, Gitea, GLPI, phpIPAM...) peuvent prendre plusieurs secondes chacun — jamais
    bloquer le rendu initial de la page dessus."""
    health_checks = [check_postgres(db), check_redis(), check_worker()]
    connector_checks = await check_connectors()
    return templates.TemplateResponse(
        request, "_health_fragment.html",
        {"health_checks": health_checks, "connector_checks": connector_checks},
    )


@router.get("/validations")
def validations(request: Request, db: Session = Depends(get_db)):
    pending = db.scalars(
        select(VMRequest)
        .where(VMRequest.status == RequestStatus.pending_approval)
        .order_by(VMRequest.created_at.asc())
    ).all()
    recent = db.scalars(
        select(VMRequest)
        .where(VMRequest.status != RequestStatus.pending_approval)
        .order_by(VMRequest.created_at.desc())
        .limit(10)
    ).all()
    return templates.TemplateResponse(
        request, "validations.html",
        {
            "pending_approval": pending, "recent": recent,
            "pending_count": len(pending), "glpi_base_url": _glpi_base_url(),
        },
    )


@router.get("/vm/{request_id}")
def vm_detail(request_id: int, request: Request, db: Session = Depends(get_db)):
    vm_request = db.get(VMRequest, request_id)
    if vm_request is None:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    return templates.TemplateResponse(
        request, "detail.html",
        {"r": vm_request, "pending_count": pending_approval_count(db), "glpi_base_url": _glpi_base_url()},
    )


@router.get("/vm/{request_id}/status-fragment")
def vm_status_fragment(request_id: int, request: Request, db: Session = Depends(get_db)):
    vm_request = db.get(VMRequest, request_id)
    if vm_request is None:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    return templates.TemplateResponse(
        request, "_status_fragment.html", {"r": vm_request}
    )


@router.get("/vm/{request_id}/approve")
def approve_options(
    request_id: int, request: Request, db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    vm_request = db.get(VMRequest, request_id)
    if vm_request is None:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if vm_request.status != RequestStatus.pending_approval:
        raise HTTPException(status_code=409, detail="Cette demande n'est pas en attente de validation")

    playbooks = [p for p in list_playbooks() if p.enabled]
    for p in playbooks:
        p.implemented = (PLAYBOOKS_DIR / p.repo_path).exists()  # type: ignore[attr-defined]

    return templates.TemplateResponse(
        request, "approve_options.html",
        {
            "r": vm_request,
            "mandatory": [p for p in playbooks if p.category == "mandatory"],
            "optional": [p for p in playbooks if p.category == "optional"],
            "pending_count": pending_approval_count(db),
            "current_username": _admin.get("username", ""),
            "glpi_base_url": _glpi_base_url(),
        },
    )


@router.post("/vm/{request_id}/approve")
async def approve(
    request_id: int, request: Request, db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    vm_request = db.get(VMRequest, request_id)
    if vm_request is None:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if vm_request.status != RequestStatus.pending_approval:
        raise HTTPException(status_code=409, detail="Cette demande n'est pas en attente de validation")

    form = await request.form()
    checked = set(form.getlist("playbooks"))
    all_playbooks = [p for p in list_playbooks() if p.enabled]
    mandatory = [p for p in all_playbooks if p.category == "mandatory"]
    optional = [p for p in all_playbooks if p.category == "optional"]

    skipped_mandatory = [p for p in mandatory if p.key not in checked]
    if skipped_mandatory:
        confirm_username = (form.get("confirm_username") or "").strip()
        if not confirm_username or confirm_username != _admin.get("username", ""):
            return templates.TemplateResponse(
                request, "approve_options.html",
                {
                    "r": vm_request,
                    "mandatory": mandatory,
                    "optional": optional,
                    "pending_count": pending_approval_count(db),
                    "current_username": _admin.get("username", ""),
                    "glpi_base_url": _glpi_base_url(),
                    "error": (
                        "Pour décocher un playbook obligatoire (" +
                        ", ".join(p.label for p in skipped_mandatory) +
                        "), le nom d'utilisateur saisi doit correspondre exactement à ton "
                        "compte connecté (" + _admin.get("username", "") + ")."
                    ),
                },
                status_code=400,
            )

    vm_request.selected_playbooks = [p.key for p in mandatory + optional if p.key in checked]

    vm_request.status = RequestStatus.pending
    vm_request.approved_by = _admin.get("username")
    vm_request.approved_at = datetime.utcnow()
    db.commit()

    vm_queue.enqueue(process_vm_request, vm_request.id)
    notify_msg = (
        f"[Hephaestus] Demande approuvée par {vm_request.approved_by} — la VM "
        f"\"{vm_request.hostname}\" est en cours de création."
    )
    if skipped_mandatory:
        notify_msg += (
            f"\nPlaybooks obligatoires désactivés par {_admin.get('username', '?')} : "
            + ", ".join(p.label for p in skipped_mandatory)
        )
    _notify_glpi(vm_request, notify_msg)

    return RedirectResponse(url=form.get("next", f"/vm/{request_id}"), status_code=303)


@router.post("/vm/{request_id}/reject")
async def reject(
    request_id: int, request: Request, db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    vm_request = db.get(VMRequest, request_id)
    if vm_request is None:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if vm_request.status != RequestStatus.pending_approval:
        raise HTTPException(status_code=409, detail="Cette demande n'est pas en attente de validation")

    form = await request.form()
    reason = (form.get("reason") or "").strip()

    vm_request.status = RequestStatus.rejected
    vm_request.rejection_reason = reason or None
    db.commit()

    notify_msg = f"[Hephaestus] Demande refusée — la création de la VM \"{vm_request.hostname}\" n'aura pas lieu."
    if reason:
        notify_msg += f"\nMotif : {reason}"
    _notify_glpi(vm_request, notify_msg)

    return RedirectResponse(url=form.get("next", f"/vm/{request_id}"), status_code=303)
