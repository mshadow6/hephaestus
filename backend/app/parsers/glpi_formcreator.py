import html
import re

# Labels tels qu'affichés dans le contenu HTML du formulaire GLPI -> nos clés internes.
# Clés en minuscules : le matching se fait sans tenir compte de la casse (cf.
# extract_label_values), pour ne pas casser si la capitalisation du formulaire change.
FIELD_LABEL_MAP: dict[str, str] = {
    "nom de la vm": "hostname",
    "template à cloner": "os_template",
    "vcpu": "cpu",
    "ram": "ram_gb",
    "vlan": "vlan",
    "taille disque": "disk_gb",
}

# Le formulaire génère "<b>N) Label</b>: valeur<br>" pour chaque question. On matche
# sur le label (pas sur le numéro N), pour rester robuste si les questions sont
# réordonnées dans le formulaire GLPI.
_FIELD_PATTERN = re.compile(
    r"<b>\s*\d+\)\s*(?P<label>.*?)\s*</b>\s*:\s*(?P<value>.*?)\s*(?=<br\s*/?>|</p>|$)",
    re.IGNORECASE | re.DOTALL,
)


class MissingGlpiFieldError(ValueError):
    def __init__(self, missing_labels: list[str]):
        self.missing_labels = missing_labels
        super().__init__(
            "Champ(s) attendu(s) absent(s) de item.content : " + ", ".join(missing_labels)
        )


def _clean(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def normalize_vlan(raw: str) -> str | None:
    """Ne garde que le numéro du VLAN (ex: "vlan 100" ou "VLAN100" -> "100") — même
    normalisation quelle que soit la source de la demande (GLPI ou /requests/new), pour
    que la résolution de sous-réseau par VLAN (voir PhpIpamProvider._resolve_subnet) soit
    cohérente peu importe par où la demande est arrivée. Sans ça, une demande GLPI et une
    demande saisie à la main pouvaient produire des valeurs différentes pour le même VLAN
    ("100" vs "vlan 100"), qui ne matchaient pas la même config de sous-réseau."""
    match = re.search(r"\d+", raw or "")
    return match.group() if match else None


def extract_label_values(content: str) -> dict[str, str]:
    """Extrait toutes les paires label/valeur du contenu HTML, dans l'ordre du texte.

    Les labels sont normalisés en minuscules pour matcher FIELD_LABEL_MAP sans
    tenir compte de la casse.
    """
    return {
        _clean(match.group("label")).lower(): _clean(match.group("value"))
        for match in _FIELD_PATTERN.finditer(content)
    }


def parse_vm_request_fields(content: str) -> dict[str, str]:
    """Extrait et mappe les champs du formulaire GLPI vers nos clés internes.

    Lève MissingGlpiFieldError si un label attendu est absent, ou ValueError si
    une valeur ne peut pas être interprétée (ex: pas de numéro de VLAN trouvé).
    """
    raw_values = extract_label_values(content)

    missing = [label for label in FIELD_LABEL_MAP if label not in raw_values]
    if missing:
        raise MissingGlpiFieldError(missing)

    mapped = {
        FIELD_LABEL_MAP[label]: value
        for label, value in raw_values.items()
        if label in FIELD_LABEL_MAP
    }

    normalized_vlan = normalize_vlan(mapped["vlan"])
    if normalized_vlan is None:
        raise ValueError(f"Impossible d'extraire un numéro de VLAN depuis '{mapped['vlan']}'")
    mapped["vlan"] = normalized_vlan

    return mapped
