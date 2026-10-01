from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import RequestStatus


class VMRequestCreate(BaseModel):
    """Champs extraits du payload webhook GLPI/Formcreator.

    Le webhook Formcreator envoie une structure variable selon le
    formulaire ; on extrait ici les champs connus et on conserve
    l'intégralité du payload dans raw_payload pour ne rien perdre.
    """

    hostname: str
    glpi_ticket_id: str | None = None
    requested_by: str | None = None
    environment: str | None = None
    vlan: str | None = None
    cpu: int | None = None
    ram_gb: int | None = None
    disk_gb: int | None = None
    os_template: str | None = None


class VMRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hostname: str
    glpi_ticket_id: str | None
    status: RequestStatus
    ip_address: str | None
    proxmox_vmid: int | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class WebhookAck(BaseModel):
    request_id: int
    status: RequestStatus
