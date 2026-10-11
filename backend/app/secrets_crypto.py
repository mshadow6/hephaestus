"""Chiffrement au repos des champs secrets stockés sur disque (connexions providers,
LDAP, Keycloak) — Fernet (AES-128-CBC + HMAC), clé dans `.env` (`CONFIG_ENCRYPTION_KEY`,
générée une fois, jamais commitée).

Protège contre la lecture directe des fichiers de config (backup qui fuit, volume
exfiltré, clé USB de sauvegarde égarée...) — pas contre un accès complet au conteneur
applicatif lui-même, qui a de toute façon cette clé en variable d'environnement pour
pouvoir déchiffrer et utiliser ces secrets en fonctionnement (authentification réelle
auprès de Proxmox, phpIPAM, etc.) : un secret de connecteur doit rester réversible, donc
ne peut pas être haché comme un mot de passe de compte local (voir app.auth.hash_password
pour ce cas-là, différent).
"""
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    if not settings.config_encryption_key:
        raise RuntimeError(
            "CONFIG_ENCRYPTION_KEY absent de .env — requis pour chiffrer/déchiffrer les "
            "secrets de connexion. Génère-en une : python -c \"from cryptography.fernet "
            "import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(settings.config_encryption_key.encode())


def encrypt_value(value: str) -> str:
    """Chiffre `value`, ou la renvoie telle quelle si vide (pas de valeur à protéger)."""
    if not value:
        return value
    return _PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    """Déchiffre `value` si elle porte le préfixe de chiffrement, sinon la renvoie telle
    quelle (valeur en clair jamais réécrite depuis l'ajout du chiffrement, ou champ qui
    n'est pas un secret) — jamais d'erreur bloquante sur une connexion existante."""
    if not value or not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        logger.warning("Déchiffrement échoué (clé changée depuis, ou valeur corrompue) — gardée telle quelle")
        return value
