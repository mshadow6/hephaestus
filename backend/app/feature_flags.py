"""Bascules de fonctionnalités, configurables depuis /settings — un seul booléen pour
l'instant (formulaire natif de demande de VM activé ou non), même principe que
app.ldap_auth.load_ldap_config/save_ldap_config : un petit fichier YAML local, jamais
commité (choix propre à chaque installation, pas une donnée du dépôt)."""
from pathlib import Path

import yaml

FEATURES_PATH = Path("config/features.yaml")

DEFAULTS: dict = {
    # Certains utilisent uniquement GLPI (ou un autre ITSM plus tard) comme point
    # d'entrée et ne veulent pas que le formulaire natif traîne dans le menu — activé
    # par défaut pour ne rien changer au comportement des installations déjà en place.
    "native_vm_form_enabled": True,
}


def load_features() -> dict:
    if not FEATURES_PATH.exists():
        return dict(DEFAULTS)
    try:
        data = yaml.safe_load(FEATURES_PATH.read_text()) or {}
    except yaml.YAMLError:
        data = {}
    return {**DEFAULTS, **data}


def save_features(features: dict) -> None:
    FEATURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = {**load_features(), **features}
    FEATURES_PATH.write_text(yaml.safe_dump(merged, sort_keys=False))
