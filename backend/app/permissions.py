"""Vocabulaire fixe des permissions — les GROUPES eux sont librement créables par
quiconque a `manage_groups` (voir app.models.Group), mais les permissions qu'on peut leur
attribuer correspondent toujours à une action réelle gardée quelque part dans le code
(un router), jamais une chaîne arbitraire inventée à la volée côté UI."""

PERMISSIONS: dict[str, str] = {
    "approve_vm": "Approuver / rejeter les demandes de VM",
    "manage_connectors": "Gérer les connexions (IPAM, DNS, hyperviseur, SCM, ITSM...)",
    "manage_playbooks": "Gérer le catalogue de playbooks",
    "run_playbooks": "Déployer des playbooks à la demande et gérer l'inventaire",
    "manage_settings": "Gérer les réglages (TLS, LDAP, Keycloak, formulaire natif)",
    "manage_users": "Créer des comptes, réinitialiser des mots de passe, déverrouiller",
    "manage_groups": "Créer et modifier les groupes de permissions",
}

ALL_PERMISSIONS: list[str] = list(PERMISSIONS)
