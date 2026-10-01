import os
from pathlib import Path

from fastapi.templating import Jinja2Templates

STATIC_DIR = Path(__file__).resolve().parent / "static"


def static_version() -> int:
    """Cache-busting pour les fichiers statiques (CSS surtout) : mtime du fichier, pas un
    numéro à incrémenter à la main — sinon les changements de CSS ne se voient pas tant
    que le navigateur ne revalide pas son cache de lui-même (peut prendre un moment,
    surtout via un proxy/VPN)."""
    try:
        return int(os.path.getmtime(STATIC_DIR / "style.css"))
    except OSError:
        return 0

STATUS_LABELS = {
    "pending_approval": "En attente",
    "rejected": "Refusée",
    "pending": "En file",
    "ip_reserved": "IP réservée",
    "provisioning": "Provisioning",
    "vm_created": "VM créée",
    "vm_failed": "Échec VM",
    "post_install": "Post-install",
    "done": "Terminée",
    "failed": "Échec",
    "queued": "En file",
    "running": "En cours",
    "success": "Réussi",
}


def status_label(value: str) -> str:
    return STATUS_LABELS.get(value, value)


def native_vm_form_enabled() -> bool:
    from app.feature_flags import load_features
    return load_features().get("native_vm_form_enabled", True)


templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
templates.env.filters["status_label"] = status_label
templates.env.globals["static_version"] = static_version
templates.env.globals["native_vm_form_enabled"] = native_vm_form_enabled
