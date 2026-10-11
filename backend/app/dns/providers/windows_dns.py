import logging

import winrm

from ..base import DnsProvider

logger = logging.getLogger(__name__)


class WindowsDnsError(RuntimeError):
    pass


def _ps_quote(value: str) -> str:
    """Échappe une valeur pour l'insérer dans une chaîne PowerShell entre guillemets
    doubles — ` (backtick) est le caractère d'échappement de PowerShell, pas \\. Les
    valeurs viennent de VMRequest.hostname/l'IP réservée (jamais une saisie libre dans un
    champ texte arbitraire), mais on échappe quand même : un hostname vient du formulaire
    natif de demande de VM, rempli par un utilisateur, pas une valeur garantie sûre."""
    return value.replace("`", "``").replace('"', '`"').replace("$", "`$")


class WindowsDnsProvider(DnsProvider):
    """DNS intégré Active Directory, via WinRM + PowerShell (module DnsServer) —
    `Add-DnsServerResourceRecordA` / `Remove-DnsServerResourceRecord`, exécutés à distance
    sur le contrôleur de domaine via un compte de service.

    Non testé contre un vrai serveur Windows DNS au moment où ce code est écrit (pas
    d'infra Windows disponible) — à valider en conditions réelles dès qu'un serveur est
    accessible, même principe que Technitium/phpIPAM/Proxmox avant leur validation.
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.server = self.config.get("server", "")
        self.zone = self.config.get("zone", "")
        self.username = self.config.get("username", "")
        self.password = self.config.get("password", "")
        missing = [
            k for k, v in {
                "server": self.server, "zone": self.zone,
                "username": self.username, "password": self.password,
            }.items() if not v
        ]
        if missing:
            raise WindowsDnsError(f"Connexion DNS Windows incomplète - champs manquants : {', '.join(missing)}")

    def _session(self) -> winrm.Session:
        # HTTP (5985) + transport NTLM : les messages sont signés/scellés par NTLM par
        # défaut dans pywinrm (pas en clair sur le réseau), mais pas aussi robuste que du
        # WinRM sur HTTPS (5986) avec un vrai certificat — à activer côté serveur Windows
        # si l'environnement le permet, sans rien à changer ici (juste l'URL).
        endpoint = f"http://{self.server}:5985/wsman"
        return winrm.Session(endpoint, auth=(self.username, self.password), transport="ntlm")

    def _run_ps(self, script: str) -> None:
        result = self._session().run_ps(script)
        if result.status_code != 0:
            stderr = (result.std_err or b"").decode(errors="replace")
            raise WindowsDnsError(f"Commande PowerShell échouée (code {result.status_code}) : {stderr[:500]}")

    def create_record(self, hostname: str, ip: str) -> None:
        script = (
            f'Add-DnsServerResourceRecordA -ZoneName "{_ps_quote(self.zone)}" '
            f'-Name "{_ps_quote(hostname)}" -IPv4Address "{_ps_quote(ip)}" '
            f'-ComputerName "{_ps_quote(self.server)}" -CreatePtr -AllowUpdateAny'
        )
        self._run_ps(script)
        logger.info("[WindowsDNS] create_record(%s.%s -> %s) OK", hostname, self.zone, ip)

    def delete_record(self, hostname: str, ip: str) -> None:
        script = (
            f'Remove-DnsServerResourceRecord -ZoneName "{_ps_quote(self.zone)}" -RRType A '
            f'-Name "{_ps_quote(hostname)}" -RecordData "{_ps_quote(ip)}" '
            f'-ComputerName "{_ps_quote(self.server)}" -Force'
        )
        self._run_ps(script)
        logger.info("[WindowsDNS] delete_record(%s.%s -> %s) OK", hostname, self.zone, ip)
