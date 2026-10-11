# Sécurité — état réel au 2026-10-06

Document honnête sur ce qui est solide, ce qui est un compromis assumé, et ce qui est un
vrai manque — pas une liste marketing. Écrit après audit du code (pas une estimation).

**Modèle de menace implicite** : outil self-hosted pour un labo/une petite équipe de
confiance, sur un réseau qu'on contrôle (LAN ou VPN) — pas conçu pour être exposé
directement sur Internet sans réflexion supplémentaire (voir "Manques réels" plus bas).

## Authentification et autorisation

- Mots de passe hashés avec **bcrypt** (coût adaptatif), jamais en clair en base.
- **Groupes de permissions personnalisables (2026-10-06)**, remplace l'ancien système à
  deux rôles fixes (`admin`/`viewer`) : 7 permissions fixes rattachées à de vraies actions
  du code (`approve_vm`, `manage_connectors`, `manage_playbooks`, `run_playbooks`,
  `manage_settings`, `manage_users`, `manage_groups`), combinables librement dans des
  groupes créés à la demande (`/settings/groups`). `Admin` (toutes permissions) et
  `Invité` (aucune) sont des groupes système protégés — ni supprimables ni modifiables —
  pour garantir qu'il reste toujours un groupe avec tous les droits. `manage_groups` est
  elle-même une permission attribuable : peut être déléguée à un groupe non-admin.
  Appliqué via `require_permission(...)` (dépendance FastAPI paramétrée) sur chaque route
  sensible, jamais une vérification ad-hoc dispersée dans le code. Permissions figées dans
  la session à la connexion — un changement de groupe ne prend effet qu'à la prochaine
  connexion de l'utilisateur concerné.
- **Changement de mot de passe en libre-service (2026-10-06)** : `/settings/password`,
  accessible à tout utilisateur connecté (comptes LDAP/Keycloak explicitement bloqués avec
  message clair, leur mot de passe n'est pas stocké ici).
- LDAP supporté en plus du compte local : premier login LDAP réussi crée un compte
  "fantôme" sans mot de passe stocké (l'authentification repasse par l'annuaire à chaque
  connexion), groupe `Invité` par défaut (le plus restrictif), à changer explicitement
  depuis `/settings/users`. Keycloak (OIDC) pareil, groupe par défaut configurable.
- **Canal auxiliaire de timing corrigé** : un identifiant inexistant répondait plus vite
  qu'un mauvais mot de passe sur un compte existant (pas de calcul bcrypt dans ce cas) —
  permettait en théorie d'énumérer les comptes valides en mesurant le temps de réponse.
  `bcrypt.checkpw` tourne maintenant systématiquement (contre un hash factice si le compte
  n'existe pas), temps de réponse homogène.
- Durée de session réduite à **12h** (le défaut de la librairie est 14 jours) — une
  session admin qui traîne deux semaines sur un poste est un risque inutile pour un outil
  qui peut déclencher de vraies créations de VM.
- Actions sensibles protégées par confirmation explicite du nom d'utilisateur (pas une
  simple case à cocher) : décocher un playbook obligatoire à l'approbation, activer/
  désactiver une entrée du catalogue — tracé nommément, pas juste "quelqu'un a cliqué".
- **Verrouillage anti-bruteforce**, sur les comptes locaux ET LDAP : 5 échecs ->
  verrouillage temporaire 1 min, 5 échecs de plus (10 au total) -> compte désactivé,
  réactivation manuelle par un admin (`/settings/users`). Le tout premier admin créé à
  `/setup` est marqué "compte de secours" (`is_protected`) : reste soumis au verrouillage
  temporaire (donc toujours protégé contre le bruteforce), mais jamais désactivable
  définitivement par ce mécanisme — pour ne jamais perdre tout accès à sa propre
  installation. Limite assumée : un attaquant peut délibérément faire verrouiller le
  compte de quelqu'un d'autre en multipliant les échecs sur son identifiant (effet de
  bord inhérent à tout verrouillage par compte, pas spécifique à cette implémentation) ;
  pas de limitation par IP source en complément pour l'instant.
- **Journal d'audit** : fichier dédié `/app/logs/audit.log` (JSON-lines, rotation
  automatique à 10 Mo x5), persistant (volume Docker, survit aux redémarrages) —
  connexions réussies/échouées, verrouillages temporaires, désactivations/réactivations
  de compte, déconnexions, et chaque requête HTTP (méthode, chemin, statut, utilisateur,
  IP réelle via `X-Forwarded-For` derrière Caddy, durée). Consultable via
  `docker compose exec backend tail -f /app/logs/audit.log`. Pas encore de vue dédiée
  côté dashboard — fichier brut pour l'instant, à parser/grep.

## Secrets

- **Chiffrement au repos (2026-10-06)** des champs secrets de `config/providers/*.yaml`,
  `config/ldap.yaml` et `config/keycloak.yaml` (`app.secrets_crypto`, Fernet) — un token/
  mot de passe de connecteur ne peut pas être haché comme un mot de passe de compte (il
  doit rester réversible pour que l'app s'authentifie réellement auprès du service tiers),
  donc chiffré avec une clé dédiée (`CONFIG_ENCRYPTION_KEY`, `.env`) : illisible si
  quelqu'un ne récupère que ces fichiers (backup qui fuit, volume exfiltré), pas protégé
  contre un accès complet au conteneur applicatif (qui a cette clé pour fonctionner). Au
  passage, un champ `app_code` phpIPAM orphelin (jamais lu par le code, résidu d'une
  config antérieure) a été supprimé plutôt que chiffré pour rien.
- `.env`, `.secrets/`, les connexions réelles (`config/providers/*.yaml`),
  `config/ldap.yaml`, `config/keycloak.yaml` et l'inventaire réel (`config/inventory.yml`)
  sont **gitignorés** — jamais dans l'historique du dépôt (vérifié par un clone propre +
  `git grep` avant chaque publication, voir le journal de dev).
- Clés privées `.secrets/*` en permissions `600` (propriétaire seul), montées `:ro`
  (lecture seule) dans les conteneurs qui en ont besoin.
- **Deux clés SSH séparées par principe** : une pour récupérer le dépôt de playbooks
  (lecture seule, deploy key), une autre pour se connecter aux VMs à déployer — jamais la
  même, pour qu'une compromission de l'une n'expose pas l'autre usage.
- Les champs mot de passe des connexions (phpIPAM, GLPI, Proxmox, **LDAP, Keycloak —
  corrigé le 2026-10-06, oubliés lors du premier passage**) ne sont **jamais pré-remplis
  en clair** dans le HTML du formulaire d'édition : laissés vides tant qu'on ne les change
  pas, fusionnés côté serveur à l'enregistrement.
- **Token API Technitium DNS retiré de l'URL (2026-10-06)** : envoyé via l'en-tête
  `Authorization: Bearer`, plus en paramètre `?token=` — trouvé en conditions réelles que
  httpx logue chaque requête sortante (méthode + URL complète) au niveau INFO, donc le
  token se serait retrouvé en clair dans les logs persistants du worker à chaque création
  de VM. Les autres connecteurs (Proxmox, phpIPAM, GLPI) étaient déjà corrects (token/mot
  de passe en en-tête ou corps de requête, jamais dans l'URL) — vérifié un par un.
- **Bug corrigé (2026-10-06)** : `save_connection` désactivait l'ancienne connexion active
  d'une catégorie *avant* de valider/écrire la nouvelle — une erreur de validation sur la
  nouvelle (ex: nom invalide) laissait la catégorie sans aucune connexion active, en
  silence. Ordre inversé : on écrit la nouvelle d'abord, on ne désactive les autres que si
  ça a réussi.
- `ANSIBLE_ADMIN_PASSWORD` reste en clair dans `.env` — nécessaire tel quel pour générer
  le hash déployé sur les VMs ET servir de mot de passe `sudo` (`become`) ; pas de vault
  pour l'instant. Limite connue, pas corrigée.
- **`REDIS_PASSWORD` (2026-10-06)** passé en argument de ligne de commande à `redis-server`
  (`docker-compose.yml`) — visible via `docker inspect`/`ps` *depuis l'intérieur du host
  Docker*, pas depuis le réseau. Pas idéal (un `redis.conf` monté en fichier l'éviterait)
  mais très largement suffisant vu que Redis n'est de toute façon plus joignable que par
  les autres conteneurs du projet (voir plus bas). Pas corrigé plus avant, compromis
  assumé.
- sudo sur les VMs provisionnées demande un mot de passe — **pas de NOPASSWD** —
  volontairement : une clé/session compromise ne suffit pas à passer root sans ce mot de
  passe, et l'escalade se journalise côté VM.

## Réseau et exposition

- **Correctif important (2026-10-06)** : Postgres (`5432`), Redis (`6379`) et le backend
  FastAPI (`8000`) étaient exposés directement sur l'hôte/le LAN en plus de Caddy (`80`/
  `443`), **sans que rien n'en ait besoin** — tout le trafic inter-conteneurs passe par le
  réseau Docker interne, qui ne dépend pas de ces directives `ports:`. Conséquences
  réelles avant correctif :
  - **Redis sans aucun mot de passe**, joignable depuis tout le LAN : accès en lecture/
    écriture à la file de jobs RQ, y compris potentiellement le mot de passe root éphémère
    d'une VM en cours de provisioning, et possibilité théorique d'y injecter des jobs
    arbitraires exécutés par le worker.
  - Le backend joignable en direct contournait entièrement Caddy : pas de HTTPS, pas des
    en-têtes de sécurité, et surtout un `X-Forwarded-For` falsifiable à volonté par
    n'importe qui en contactant le port 8000 directement (le journal d'audit s'y fie pour
    l'IP réelle).
  - Postgres joignable en direct : surface d'attaque inutile (force brute sur les
    identifiants DB, même si le mot de passe lui-même n'est pas faible par défaut).
  
  **Corrigé** : plus aucune de ces trois directives `ports:` — seul Caddy (`80`/`443`)
  reste exposé. Redis a désormais un mot de passe obligatoire (`REDIS_PASSWORD`, généré
  et stocké dans `.env`).
- **`X-Forwarded-For` / confiance proxy** : `client_ip()` (journal d'audit) prend le
  premier hôte de l'en-tête `X-Forwarded-For`, en confiance totale — sûr tant que Caddy
  est le *seul* chemin d'entrée (vrai depuis le correctif ci-dessus). **Si tu places un
  autre reverse proxy devant Caddy** (ce que l'app doit supporter), configure le
  `trusted_proxies` de Caddy pour qu'il n'accepte un `X-Forwarded-For` entrant que de ce
  proxy-là — sinon un client pourrait forger cet en-tête et fausser le journal d'audit.
  Pas automatisé côté app (dépend de l'IP du proxy externe, propre à chaque déploiement).

## Transport et en-têtes HTTP

- En-têtes de sécurité basiques ajoutés côté proxy (Caddy) : `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`.
- Cookie de session : `SameSite=Lax` (défaut de la librairie) — protection raisonnable
  contre le CSRF pour les requêtes POST cross-site (le cookie n'est pas envoyé), pas de
  jeton CSRF explicite en plus. **Pas de flag `Secure`** : le cookie serait refusé par le
  navigateur en HTTP si on le forçait, et HTTPS reste désactivé par défaut sur ce projet —
  compromis assumé plutôt qu'une bascule dynamique non triviale (le HTTPS s'active/
  désactive à chaud côté Caddy, sans redémarrer le backend qui configure ce flag une fois
  au démarrage).
- **`--proxy-headers` activé sur uvicorn (2026-10-06)** : sans ça, `request.url.scheme`
  restait toujours `http` même avec le HTTPS activé côté Caddy (la connexion interne
  Caddy -> backend reste en clair, seule la partie externe est chiffrée) — cassait
  silencieusement le `redirect_uri` envoyé à Keycloak (généré en `http://` au lieu de
  `https://`, rejeté par le realm car ne correspondant pas à l'URL enregistrée). Sûr
  d'activer une confiance large (`--forwarded-allow-ips='*'`) maintenant que seul Caddy
  peut atteindre le backend (voir "Réseau et exposition").
- **HTTPS activable depuis l'interface** : `/settings/tls` — certificat auto-signé généré
  en un clic (`cryptography`, RSA 2048, valide 825 jours) ou import d'un vrai certificat
  (validé : la clé doit correspondre au certificat avant installation). Bascule poussée en
  direct à Caddy via son API d'admin (jamais exposée hors du réseau interne
  docker-compose — pas de `ports:` dessus, voir `Caddyfile`), pas de redémarrage de
  conteneur nécessaire. Redirection HTTP -> HTTPS automatique une fois activé. Limite
  assumée : si le conteneur proxy redémarre pour une autre raison, il recharge le
  Caddyfile statique du dépôt (HTTP) et perd la bascule — revenir sur la page et cliquer
  "Réappliquer" suffit, pas besoin de reconfigurer le certificat. **Reste désactivé par
  défaut** (comportement du dépôt inchangé tant que personne ne l'active).
- **Pages d'erreur (2026-10-06)** : une erreur HTTP (400/403/404...) renvoyait du JSON brut
  sans aucun style, y compris à un navigateur — remplacé par une vraie page HTML (ou un
  petit fragment stylé pour les requêtes HTMX), sauf pour le webhook GLPI qui reste en
  JSON (c'est une machine qui l'appelle, pas un navigateur).

## Base de données et code applicatif

- SQLAlchemy (ORM) partout, aucune requête SQL construite par concaténation de chaînes —
  pas de surface d'injection SQL identifiée.
- Pas de `CORS` configuré (aucun middleware CORS ajouté) — pas d'accès cross-origin à
  l'API par défaut.

## Manques réels (pas corrigés, à savoir avant un usage sérieux)

- **Conteneurs backend/worker tournent en root** (pas de directive `USER` dans le
  `Dockerfile`) — pratique courante pour un petit projet mais pas la meilleure : en cas de
  compromission applicative, l'attaquant a les droits root *dans le conteneur* (pas sur
  l'hôte directement, Docker isole ça, mais c'est quand même un cran de moins que
  nécessaire). Pas corrigé — demanderait de revoir les permissions sur les clés SSH
  montées et l'exécution d'Ansible/Terraform avec un utilisateur dédié.
- Pas de verrouillage/validation TLS par défaut sur les connexions Proxmox
  (`insecure_tls: true` par défaut) — assumé pour des certificats auto-signés de labo,
  à resserrer si ton instance a un vrai certificat.
- Pas de jeton CSRF explicite (voir "Transport et en-têtes HTTP") — `SameSite=Lax` couvre
  l'essentiel mais pas un vrai double-submit token.
- Si tu places un second reverse proxy devant Caddy, pense à son `trusted_proxies` (voir
  "Réseau et exposition") — pas automatisé, dépend de ton déploiement.

## En résumé

Solide sur l'essentiel pour un usage labo/petite équipe de confiance (hashing correct,
permissions granulaires appliquées partout, secrets jamais versionnés, clés séparées par
usage, canal de timing corrigé, verrouillage anti-bruteforce, HTTPS activable en un clic
et fonctionnel derrière un proxy, Redis/Postgres/backend plus exposés qu'à Caddy). Le
manque restant le plus concret est l'exécution des conteneurs en root — pas critique à ce
stade, mais pas la meilleure pratique.
