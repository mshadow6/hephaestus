"""Gestion du certificat TLS et bascule HTTP/HTTPS du proxy Caddy — pilotée depuis
/settings/tls plutôt qu'en éditant des fichiers à la main. La config est poussée à Caddy
via son API d'admin (voir Caddyfile — jamais exposée hors du réseau interne
docker-compose), pas besoin de redémarrer le conteneur proxy à chaque changement.

Limite assumée : si le conteneur proxy redémarre pour une autre raison (mise à jour de
l'image, redémarrage manuel de ce seul conteneur), il recharge le Caddyfile statique du
dépôt (HTTP uniquement) et perd la config poussée en direct — revenir sur cette page et
cliquer "Réappliquer" suffit à la relancer, pas besoin de reconfigurer le certificat.
"""
import datetime
import ipaddress
import json
import logging
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

TLS_DIR = Path("/data/tls")
CERT_PATH = TLS_DIR / "cert.pem"
KEY_PATH = TLS_DIR / "key.pem"
STATE_PATH = TLS_DIR / "state.json"
CADDY_ADMIN_URL = "http://proxy:2019"


class TlsError(RuntimeError):
    pass


def _security_headers() -> str:
    return (
        "\theader {\n"
        "\t\tX-Content-Type-Options nosniff\n"
        "\t\tX-Frame-Options DENY\n"
        "\t\tReferrer-Policy strict-origin-when-cross-origin\n"
        "\t}\n"
    )


# Répété dans CHAQUE config poussée (pas juste le Caddyfile statique initial) : sans ça,
# Caddy retombe sur son admin par défaut (loopback uniquement dans son propre conteneur)
# dès le premier /load, et le backend ne peut plus jamais le repousser ensuite (trouvé en
# testant : le 2e appel à apply() échouait en Connection refused).
_GLOBAL_OPTIONS = "{\n\tadmin 0.0.0.0:2019\n}\n\n"


def _http_only_caddyfile() -> str:
    return f"{_GLOBAL_OPTIONS}:80 {{\n{_security_headers()}\treverse_proxy backend:8000\n}}\n"


def _https_caddyfile() -> str:
    return (
        f"{_GLOBAL_OPTIONS}"
        f":443 {{\n"
        f"\ttls {CERT_PATH} {KEY_PATH}\n"
        f"{_security_headers()}"
        f"\treverse_proxy backend:8000\n"
        f"}}\n"
        f":80 {{\n"
        f"\tredir https://{{host}}{{uri}} permanent\n"
        f"}}\n"
    )


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"enabled": False}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"enabled": False}


def _save_state(enabled: bool) -> None:
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"enabled": enabled}))


def cert_info() -> dict | None:
    if not CERT_PATH.exists():
        return None
    try:
        cert = x509.load_pem_x509_certificate(CERT_PATH.read_bytes())
    except ValueError:
        return None
    return {
        "subject": cert.subject.rfc4514_string(),
        "not_valid_after": cert.not_valid_after_utc,
        "not_valid_before": cert.not_valid_before_utc,
        "self_signed": cert.issuer == cert.subject,
    }


def generate_self_signed(common_name: str) -> None:
    """Certificat auto-signé valable 825 jours (limite acceptée par les navigateurs
    modernes pour ce type de certificat) pour le domaine ou l'IP donné."""
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    try:
        san = x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(common_name))])
    except ValueError:
        san = x509.SubjectAlternativeName([x509.DNSName(common_name)])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )

    KEY_PATH.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_PATH.chmod(0o600)


def save_uploaded(cert_pem: str, key_pem: str) -> None:
    """Valide que le certificat et la clé fournis sont lisibles et correspondent (même
    clé publique des deux côtés) avant de les installer — mieux vaut échouer ici
    proprement que de casser le proxy avec une paire invalide."""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
    except ValueError as exc:
        raise TlsError(f"Certificat invalide : {exc}") from exc
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except ValueError as exc:
        raise TlsError(f"Clé privée invalide : {exc}") from exc

    cert_pubkey = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_pubkey = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if cert_pubkey != key_pubkey:
        raise TlsError("Le certificat et la clé privée ne correspondent pas.")

    TLS_DIR.mkdir(parents=True, exist_ok=True)
    CERT_PATH.write_text(cert_pem)
    KEY_PATH.write_text(key_pem)
    KEY_PATH.chmod(0o600)


def apply(enabled: bool) -> None:
    """Pousse la config à Caddy en direct (API d'admin, aucun redémarrage de conteneur
    nécessaire) et mémorise l'état voulu (voir le disclaimer en tête de fichier)."""
    if enabled and not (CERT_PATH.exists() and KEY_PATH.exists()):
        raise TlsError("Aucun certificat installé — génères-en un ou importe le tien d'abord.")

    caddyfile = _https_caddyfile() if enabled else _http_only_caddyfile()
    try:
        resp = httpx.post(
            f"{CADDY_ADMIN_URL}/load",
            content=caddyfile.encode(),
            headers={"Content-Type": "text/caddyfile"},
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise TlsError(f"Impossible d'appliquer la config à Caddy : {exc}") from exc

    _save_state(enabled)
    logger.info("[tls] Configuration Caddy appliquée (HTTPS %s)", "activé" if enabled else "désactivé")
