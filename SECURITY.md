# Sécurité — état réel au 2026-10-01

Document honnête sur ce qui est solide, ce qui est un compromis assumé, et ce qui est un
vrai manque — pas une liste marketing. Écrit après audit du code (pas une estimation).

**Modèle de menace implicite** : outil self-hosted pour un labo/une petite équipe de
confiance, sur un réseau qu'on contrôle (LAN ou VPN) — pas conçu pour être exposé
directement sur Internet sans réflexion supplémentaire (voir "Manques réels" plus bas).

## Authentification et autorisation

- Mots de passe hashés avec **bcrypt** (coût adaptatif), jamais en clair en base.
- Deux rôles (`admin`/`viewer`), appliqués via des dépendances FastAPI explicites
  (`require_login`/`require_admin`) sur chaque route sensible — pas une vérification
  ad-hoc dispersée dans le code.
- LDAP supporté en plus du compte local : premier login LDAP réussi crée un compte
  "fantôme" sans mot de passe stocké (l'authentification repasse par l'annuaire à chaque
  connexion), rôle `viewer` par défaut (le plus restrictif), à monter en `admin`
  explicitement depuis `/settings/users`.
- **Canal auxiliaire de timing corrigé (2026-10-01)** : avant, un identifiant inexistant
  répondait nettement plus vite qu'un mauvais mot de passe sur un compte existant (pas de
  calcul bcrypt dans ce cas) — permettait en théorie d'énumérer les comptes valides en
  mesurant le temps de réponse. `bcrypt.checkpw` tourne maintenant systématiquement
  (contre un hash factice si le compte n'existe pas), temps de réponse homogène.
- Durée de session réduite à **12h** (le défaut de la librairie est 14 jours) — une
  session admin qui traîne deux semaines sur un poste est un risque inutile pour un outil
  qui peut déclencher de vraies créations de VM.
- Actions sensibles protégées par confirmation explicite du nom d'utilisateur (pas une
  simple case à cocher) : décocher un playbook obligatoire à l'approbation, activer/
  désactiver une entrée du catalogue — tracé nommément, pas juste "quelqu'un a cliqué".
- **Verrouillage anti-bruteforce (2026-10-01)**, sur les comptes locaux ET LDAP : 5 échecs
  -> verrouillage temporaire 1 min, 5 échecs de plus (10 au total) -> compte désactivé,
  réactivation manuelle par un admin (`/settings/users`). Le tout premier admin créé à
  `/setup` est marqué "compte de secours" (`is_protected`) : reste soumis au verrouillage
  temporaire (donc toujours protégé contre le bruteforce), mais jamais désactivable
  définitivement par ce mécanisme — pour ne jamais perdre tout accès à sa propre
  installation. Limite assumée : un attaquant peut délibérément faire verrouiller le
  compte de quelqu'un d'autre en multipliant les échecs sur son identifiant (effet de
  bord inhérent à tout verrouillage par compte, pas spécifique à cette implémentation) ;
  pas de limitation par IP source en complément pour l'instant.

## Secrets

- `.env`, `.secrets/`, les connexions réelles (`config/providers/*.yaml`),
  `config/ldap.yaml` et l'inventaire réel (`config/inventory.yml`) sont **gitignorés** —
  jamais dans l'historique du dépôt (vérifié par un clone propre + `git grep` avant
  chaque publication, voir le journal de dev).
- Clés privées `.secrets/*` en permissions `600` (propriétaire seul), montées `:ro`
  (lecture seule) dans les conteneurs qui en ont besoin.
- **Deux clés SSH séparées par principe** : une pour récupérer le dépôt de playbooks
  (lecture seule, deploy key), une autre pour se connecter aux VMs à déployer — jamais la
  même, pour qu'une compromission de l'une n'expose pas l'autre usage.
- Les champs mot de passe des connexions (phpIPAM, GLPI, Proxmox...) ne sont **jamais
  pré-remplis en clair** dans le HTML du formulaire d'édition (un bug réel de ce genre a
  été trouvé et corrigé tôt dans le développement — voir le journal) : laissés vides tant
  qu'on ne les change pas, fusionnés côté serveur à l'enregistrement.
- `ANSIBLE_ADMIN_PASSWORD` reste en clair dans `.env` — nécessaire tel quel pour générer
  le hash déployé sur les VMs ET servir de mot de passe `sudo` (`become`) ; pas de vault
  pour l'instant. Limite connue, pas corrigée.
- sudo sur les VMs provisionnées demande un mot de passe — **pas de NOPASSWD** —
  volontairement : une clé/session compromise ne suffit pas à passer root sans ce mot de
  passe, et l'escalade se journalise côté VM.

## Transport et en-têtes HTTP

- En-têtes de sécurité basiques ajoutés côté proxy (Caddy) : `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`.
- Cookie de session : `SameSite=Lax` (défaut de la librairie) — protection raisonnable
  contre le CSRF pour les requêtes POST cross-site (le cookie n'est pas envoyé), pas de
  jeton CSRF explicite en plus.
- **HTTPS activable depuis l'interface (2026-10-01)** : `/settings/tls` — certificat
  auto-signé généré en un clic (`cryptography`, RSA 2048, valide 825 jours) ou import
  d'un vrai certificat (validé : la clé doit correspondre au certificat avant
  installation). Bascule poussée en direct à Caddy via son API d'admin (jamais exposée
  hors du réseau interne docker-compose — pas de `ports:` dessus, voir `Caddyfile`), pas
  de redémarrage de conteneur nécessaire. Redirection HTTP -> HTTPS automatique une fois
  activé. Limite assumée : si le conteneur proxy redémarre pour une autre raison, il
  recharge le Caddyfile statique du dépôt (HTTP) et perd la bascule — revenir sur la page
  et cliquer "Réappliquer" suffit, pas besoin de reconfigurer le certificat. **Reste
  désactivé par défaut** (comportement du dépôt inchangé tant que personne ne l'active).

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
- Pas de journal d'audit dédié aux tentatives de connexion échouées (juste les logs
  applicatifs bruts, pas une vue dédiée côté dashboard).

## En résumé

Solide sur l'essentiel pour un usage labo/petite équipe de confiance (hashing correct,
rôles appliqués partout, secrets jamais versionnés, clés séparées par usage, canal de
timing corrigé, verrouillage anti-bruteforce, HTTPS activable en un clic). Le manque
restant le plus concret est l'exécution des conteneurs en root — pas critique à ce stade,
mais pas la meilleure pratique.
