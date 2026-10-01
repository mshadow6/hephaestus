# Hephaestus

Provisioning de VM self-hosted : demande (GLPI ou formulaire natif) → validation humaine
→ réservation IP (IPAM) → création VM (Terraform) → post-install (Ansible). Voir
`README.md` pour le Quickstart, `SECURITY.md` pour l'état de sécurité réel,
`JOURNAL.md` pour l'historique détaillé des décisions (ne pas le relire par défaut, trop
long — seulement si besoin du "pourquoi" d'un choix ancien).

**Scope v1** : Proxmox + Linux uniquement, testé de bout en bout sur une vraie infra.
VMware/Windows restent à l'état de brouillon (voir plus bas).

## Stack

- Backend : FastAPI + SQLAlchemy 2.0 + Alembic (migrations) + psycopg3, Jinja2 (HTML
  server-rendered, pas de SPA), HTMX pour les mises à jour partielles
- Jobs async : Redis + RQ (`backend/app/worker/tasks.py`)
- Provisioning : Terraform (`bpg/proxmox`) + Ansible (playbooks hébergés sur un dépôt Git
  **séparé**, voir plus bas — pas dans ce repo)
- Proxy : Caddy (HTTPS optionnel, activable depuis `/settings/tls`)

## Commandes

```bash
docker compose up -d --build        # tout démarrer (migrations auto au démarrage du backend)
docker compose build backend        # rebuild après modif Python
docker compose logs backend worker  # logs
docker compose exec backend python -m app.cli connection --help   # CLI connexions
docker compose exec backend python -m app.cli tls --help          # CLI HTTPS
docker compose exec backend tail -f /app/logs/audit.log           # journal d'audit (connexions, requêtes)
```

Pas de suite de tests automatisés formelle — vérification systématique via
`fastapi.testclient.TestClient` avec `app.dependency_overrides[require_admin]`/
`[require_login]`, exécuté directement dans le conteneur (`docker compose exec backend
python3 -c "..."`). Toujours tester une fonctionnalité réseau/DB contre le vrai état
(vraie connexion, vrai cluster Proxmox) quand c'est possible, pas juste en théorie — ça a
trouvé plusieurs bugs réels cette session (timeout non rattrapé, bug Caddy SELinux,
admin API Caddy qui se réinitialise).

## Conventions

- **Commentaires et chaînes utilisateur en français**, expliquant le POURQUOI (contrainte,
  retour utilisateur, bug passé) — pas le quoi. Code (noms de variables/fonctions) en
  anglais.
- **Providers pluggables** (pattern ABC + factory) pour IPAM (`app/ipam/`) et DNS
  (`app/dns/`) — un seul vrai provider implémenté par catégorie aujourd'hui (phpIPAM,
  rien pour DNS), les autres sont des stubs. **L'hyperviseur n'est PAS encore abstrait**
  (`app/provisioning/terraform_runner.py` est câblé en dur pour Proxmox) — à construire
  sur le même modèle si VMware/Hyper-V doivent être ajoutés un jour.
- **Connexions** (`app/connections/`) : un type par provider dans `types.py`
  (`ConnectionField` liste les champs), stockage YAML un fichier par connexion dans
  `config/providers/*.yaml` (gitignoré, jamais commité). CLI et interface web
  interchangeables (`app/cli.py` ↔ `/connections`).
- **Jamais de secret pré-rempli en HTML** — un champ mot de passe reste vide à l'édition,
  fusionné côté serveur si laissé vide (bug réel trouvé et corrigé tôt dans le projet).
- **Deux clés SSH séparées par principe** : une pour récupérer le dépôt de playbooks
  (lecture seule), une autre pour déployer sur les VMs cibles — jamais la même.
- **GLPI n'est qu'une source de demandes parmi d'autres** : le formulaire natif
  (`/requests/new`) et le webhook GLPI créent le même `VMRequest` et suivent le même
  pipeline, aucun traitement spécial pour l'un ou l'autre. Le formulaire natif est
  désactivable depuis `/settings` sans affecter GLPI.
- **Le dépôt de playbooks Ansible est externe** (Gitea, `homelab-playbooks`), cloné à la
  volée par l'app via deploy key SSH lecture seule — jamais dans ce repo. Avant de
  pousser un nouveau playbook sur sa branche `main`, passer par une branche
  `draft/...` si le contenu n'a pas été testé contre un vrai hôte (voir
  `draft/vmware-windows`, `draft/catalog-security-update-resize`) — ce dépôt est public,
  du contenu cassé sur `main` a un vrai impact.
- **Ne jamais approuver une VM soi-même en testant** — l'approbation est le choix de
  l'utilisateur, pas du code à exercer automatiquement. Tester la logique en isolation
  (DB directe, TestClient) sans jamais appeler `vm_queue.enqueue`/les endpoints
  d'approbation réels avec une vraie intention de créer une VM.

## État réel (ce qui marche vs ce qui reste)

**Marche, testé en conditions réelles** : pipeline complet sur Proxmox+phpIPAM+GLPI+Gitea,
auth locale+LDAP+verrouillage anti-bruteforce, HTTPS activable, journal d'audit, catalogue
de playbooks, inventaire structuré par groupes, déploiement ad-hoc avec sortie en direct,
résolution de template Proxmox en direct (plus de liste codée en dur), sous-réseaux IPAM
multi-VLAN.

**Pas fait / brouillon non testé** :
- VMware/vCenter (connecteur, création de VM) — rien côté app, juste 2 playbooks
  Ansible brouillon jamais exécutés (branche Gitea séparée)
- Post-install Windows (WinRM) — idem, brouillon jamais testé
- Agrandissement de disque générique (LVM/partition) — écrit mais jamais exécuté contre
  un hôte réel
- Installation automatisée depuis un ISO (préseed/kickstart) — évoqué, pas commencé,
  chantier à part entière si repris un jour
- 5 playbooks du catalogue encore vides (edr, cyberwatch, zabbix...) — volontaire,
  dépendent de l'outillage propre à chaque infra

## Sécurité

Voir `SECURITY.md` pour le détail — app non conçue pour être exposée directement sur
Internet sans réflexion supplémentaire (voir "Manques réels" de ce fichier). Point le
plus notable encore ouvert : conteneurs backend/worker tournent en root.
