from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RequestStatus, VMRequest
from app.parsers.glpi_formcreator import parse_vm_request_fields
from app.schemas import VMRequestCreate, WebhookAck

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/glpi", response_model=WebhookAck, status_code=202)
async def receive_glpi_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.json()

    item = raw_body.get("item")
    if not isinstance(item, dict):
        raise HTTPException(status_code=422, detail="Payload invalide : clé 'item' manquante ou invalide")

    content = item.get("content")
    if not isinstance(content, str):
        raise HTTPException(
            status_code=422, detail="Payload invalide : 'item.content' manquant ou invalide"
        )

    try:
        fields = parse_vm_request_fields(content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        payload = VMRequestCreate.model_validate(fields)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    vm_request = VMRequest(
        hostname=payload.hostname,
        glpi_ticket_id=str(item["id"]) if item.get("id") is not None else None,
        requested_by=payload.requested_by,
        environment=payload.environment,
        vlan=payload.vlan,
        cpu=payload.cpu,
        ram_gb=payload.ram_gb,
        disk_gb=payload.disk_gb,
        os_template=payload.os_template,
        raw_payload=raw_body,
        status=RequestStatus.pending_approval,
    )
    db.add(vm_request)
    db.commit()
    db.refresh(vm_request)

    # Pas d'enqueue ici : la VM attend une validation humaine (dashboard/email)
    # avant que le provisioning ne démarre — voir app.routers.dashboard.approve.

    return WebhookAck(request_id=vm_request.id, status=vm_request.status)
