from dataclasses import dataclass, field


@dataclass
class ConnectionField:
    name: str
    label: str
    input_type: str = "text"  # text | password | checkbox
    required: bool = True
    default: str | bool = ""
    help_text: str = ""


@dataclass
class ProviderType:
    key: str
    label: str
    category: str  # "ipam" | "dns" | "hypervisor" | "itsm" | "scm"
    fields: list[ConnectionField] = field(default_factory=list)


# Libellés + ordre d'affichage des catégories dans le sélecteur "nouvelle connexion" —
# on choisit d'abord la catégorie (Hyperviseur, IPAM, ...), puis le provider dedans,
# plutôt qu'une seule longue liste plate de tous les types mélangés.
CATEGORY_LABELS: dict[str, str] = {
    "hypervisor": "Hyperviseur",
    "ipam": "IPAM (adressage IP)",
    "dns": "DNS",
    "itsm": "ITSM (tickets)",
    "scm": "Playbooks (dépôt Git)",
}


def categories_in_use() -> list[tuple[str, str]]:
    """Catégories qui ont au moins un type de provider, dans l'ordre de CATEGORY_LABELS."""
    used = {p.category for p in PROVIDER_TYPES.values()}
    return [(key, label) for key, label in CATEGORY_LABELS.items() if key in used]


def provider_types_for_category(category: str) -> dict[str, "ProviderType"]:
    return {k: p for k, p in PROVIDER_TYPES.items() if p.category == category}


PROVIDER_TYPES: dict[str, ProviderType] = {
    "phpipam": ProviderType(
        key="phpipam",
        label="phpIPAM",
        category="ipam",
        fields=[
            ConnectionField("base_url", "URL de base", help_text="ex: http://192.0.2.10:8080"),
            ConnectionField("app_id", "App ID", help_text="ex: vmauto — app créée dans phpIPAM"),
            ConnectionField("username", "Utilisateur (login API, pas app_code — phpIPAM exige "
                                          "une vraie connexion utilisateur pour obtenir un token)"),
            ConnectionField("password", "Mot de passe", input_type="password"),
            ConnectionField("gateway", "Passerelle (sous-réseau unique, sans VLAN)", required=False,
                             help_text="ex: 192.0.2.254 — uniquement si un seul sous-réseau/VLAN à "
                                        "gérer. Pour plusieurs VLAN, laisse vide et configure-les "
                                        "sur la page \"Sous-réseaux (VLAN)\" après avoir créé la "
                                        "connexion."),
            ConnectionField("dns_servers", "Serveurs DNS (séparés par virgule)", required=False,
                             help_text="ex: 192.0.2.254"),
            ConnectionField("allocation_range_start", "Première IP allouable", required=False,
                             help_text="ex: 192.0.2.100 — jamais en dehors de cette plage "
                                        "(hors DHCP de la box, à vérifier avant de configurer)"),
            ConnectionField("allocation_range_end", "Dernière IP allouable", required=False,
                             help_text="ex: 192.0.2.200"),
        ],
    ),
    "efficientip": ProviderType(
        key="efficientip",
        label="EfficientIP",
        category="ipam",
        fields=[
            ConnectionField("base_url", "URL de base", help_text="ex: https://ddi.example.com"),
            ConnectionField("username", "Utilisateur"),
            ConnectionField("password", "Mot de passe", input_type="password"),
        ],
    ),
    "proxmox": ProviderType(
        key="proxmox",
        label="Proxmox VE",
        category="hypervisor",
        fields=[
            ConnectionField("api_url", "URL API", help_text="ex: https://192.0.2.20:8006"),
            ConnectionField("api_token", "Token API", input_type="password",
                             help_text="format user@realm!tokenid=secret"),
            ConnectionField("node", "Nœud cible"),
            ConnectionField("insecure_tls", "Ignorer la validation TLS (certificat auto-signé)",
                             input_type="checkbox", required=False, default=True),
            ConnectionField("network_bridge", "Bridge réseau", required=False, default="vmbr0"),
            ConnectionField("disk_storage", "Pool de stockage disque", required=False, default="local-lvm"),
            ConnectionField("disk_interface", "Interface disque", required=False, default="scsi0"),
        ],
    ),
    "vmware": ProviderType(
        key="vmware",
        label="VMware vCenter",
        category="hypervisor",
        fields=[
            ConnectionField("api_url", "URL vCenter", help_text="ex: https://vcenter.example.com"),
            ConnectionField("username", "Utilisateur"),
            ConnectionField("password", "Mot de passe", input_type="password"),
            ConnectionField("insecure_tls", "Ignorer la validation TLS", input_type="checkbox",
                             required=False, default=True),
        ],
    ),
    "glpi": ProviderType(
        key="glpi",
        label="GLPI",
        category="itsm",
        fields=[
            ConnectionField("base_url", "URL de base", help_text="ex: http://192.0.2.30:8081"),
            ConnectionField("client_id", "Client ID OAuth2",
                             help_text="Créé dans GLPI : Configurer > Général > Clients OAuth "
                                        "(grant type \"Mot de passe\" + scopes api/user requis)"),
            ConnectionField("client_secret", "Client secret OAuth2", input_type="password"),
            ConnectionField("username", "Utilisateur (compte de service)"),
            ConnectionField("password", "Mot de passe", input_type="password"),
        ],
    ),
    "windows_dns": ProviderType(
        key="windows_dns",
        label="DNS Windows / Active Directory",
        category="dns",
        fields=[
            ConnectionField("server", "Serveur DNS", help_text="ex: dc01.example.local"),
            ConnectionField("zone", "Zone DNS", help_text="ex: example.local"),
            ConnectionField("username", "Utilisateur (compte de service)"),
            ConnectionField("password", "Mot de passe", input_type="password"),
        ],
    ),
    "technitium": ProviderType(
        key="technitium",
        label="Technitium DNS",
        category="dns",
        fields=[
            ConnectionField("base_url", "URL de base", help_text="ex: http://192.0.2.40:5380"),
            ConnectionField("api_token", "Token API", input_type="password"),
            ConnectionField("zone", "Zone DNS", help_text="ex: example.local"),
        ],
    ),
    "bind": ProviderType(
        key="bind",
        label="BIND (nsupdate)",
        category="dns",
        fields=[
            ConnectionField("server", "Serveur DNS", help_text="ex: 192.0.2.50"),
            ConnectionField("zone", "Zone DNS", help_text="ex: example.local"),
            ConnectionField("tsig_key_name", "Nom de la clé TSIG"),
            ConnectionField("tsig_key_secret", "Secret de la clé TSIG", input_type="password"),
        ],
    ),
    "gitea": ProviderType(
        key="gitea",
        label="Gitea (repo playbooks Ansible)",
        category="scm",
        fields=[
            ConnectionField("ssh_url", "URL SSH du repo",
                             help_text="ex: ssh://git@192.0.2.10:2222/admin/my-playbooks-repo.git — "
                                        "clone en lecture seule via une deploy key dédiée (jamais de "
                                        "token en clair dans l'URL). Voir Setup > Deploy Keys sur le "
                                        "repo Gitea pour enregistrer la clé publique correspondante."),
        ],
    ),
}
