from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://vmprov:vmprov@localhost:5432/vmprovisioning"
    redis_url: str = "redis://localhost:6379/0"
    rq_queue_name: str = "vm-provisioning"

    # IPAM/DNS/hyperviseur/Gitea : plus de config statique ici — voir
    # app.connections.store.get_active_connection(). Le type ET les identifiants viennent
    # de la connexion active choisie via l'écran Connexions (ou la CLI), pas de .env.

    # Clé de signature des cookies de session (dashboard) — générée et stockée en .env
    session_secret: str = ""

    # Post-install Ansible — ceci configure comment LE PIPELINE agit sur les VMs qu'il crée
    # (pas une connexion à un système tiers, donc volontairement hors de app.connections) :
    # clé d'automatisation partagée, et compte intermédiaire déployé par deploy-ssh-keys.yml.
    ansible_ssh_private_key_path: str = "/app/.secrets/vmprov_bastion_key"
    # Clé dédiée, distincte de la précédente : sert UNIQUEMENT à récupérer (lecture seule,
    # deploy key) le repo homelab-playbooks sur Gitea — jamais à se connecter à une VM.
    # Une clé pour récupérer, une autre pour déployer, jamais la même (retour user).
    gitea_ssh_private_key_path: str = "/app/.secrets/vmprov_gitea_key"
    ansible_admin_username: str = "admin"
    # Mot de passe (en clair) du compte intermédiaire — sert à la fois à générer le hash
    # déployé par deploy-ssh-keys.yml et de mot de passe `sudo` (become) pour harden-root.yml.
    # Jamais commité : uniquement en .env.
    ansible_admin_password: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
