import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path("/app/config/playbooks")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")

CATEGORIES = ["mandatory", "optional"]
CATEGORY_LABELS = {"mandatory": "Obligatoire", "optional": "Optionnel"}


class InvalidPlaybookName(ValueError):
    pass


@dataclass
class PlaybookDef:
    key: str
    label: str
    repo_path: str  # chemin relatif dans le repo Gitea homelab-playbooks
    category: str = "optional"  # "mandatory" | "optional"
    description: str = ""
    enabled: bool = True
    order: int = 0


def _path_for(key: str) -> Path:
    if not _SLUG_RE.match(key):
        raise InvalidPlaybookName(
            "La clé doit faire 2 à 63 caractères, minuscules/chiffres/tirets uniquement."
        )
    return CONFIG_DIR / f"{key}.yaml"


def list_playbooks() -> list[PlaybookDef]:
    if not CONFIG_DIR.exists():
        return []
    items = []
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        items.append(PlaybookDef(
            key=path.stem,
            label=data.get("label", path.stem),
            repo_path=data.get("repo_path", ""),
            category=data.get("category", "optional"),
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            order=data.get("order", 0),
        ))
    return sorted(items, key=lambda p: (p.category, p.order, p.label))


def get_playbook(key: str) -> PlaybookDef | None:
    path = _path_for(key)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    return PlaybookDef(
        key=key,
        label=data.get("label", key),
        repo_path=data.get("repo_path", ""),
        category=data.get("category", "optional"),
        description=data.get("description", ""),
        enabled=data.get("enabled", True),
        order=data.get("order", 0),
    )


def save_playbook(playbook: PlaybookDef) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(playbook.key)
    payload = {
        "label": playbook.label,
        "repo_path": playbook.repo_path,
        "category": playbook.category,
        "description": playbook.description,
        "enabled": playbook.enabled,
        "order": playbook.order,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def delete_playbook(key: str) -> bool:
    path = _path_for(key)
    if not path.exists():
        return False
    path.unlink()
    return True
