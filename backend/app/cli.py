"""CLI d'administration — gestion des connexions (IPAM/DNS/hyperviseur/Gitea/...) sans
passer par le dashboard web. Même store que l'écran Connexions (`app.connections.store`) :
les deux sont interchangeables, un `connection set` ici est visible immédiatement dans
l'UI et inversement.

Usage (depuis le conteneur `backend`/`worker`, qui a `/app/config/providers` monté) :
    python -m app.cli connection types
    python -m app.cli connection list
    python -m app.cli connection show <name>
    python -m app.cli connection set <name> --type proxmox --active \
        --field api_url=https://192.0.2.20:8006 --field api_token=... --field node=pve-node-01
    python -m app.cli connection delete <name>
"""
import argparse
import sys

from app.connections.store import (
    Connection,
    InvalidConnectionName,
    delete_connection,
    get_connection,
    list_connections,
    save_connection,
)
from app.connections.types import PROVIDER_TYPES


def _coerce_field(provider_key: str, field_name: str, raw_value: str):
    provider = PROVIDER_TYPES.get(provider_key)
    field_def = None
    if provider:
        field_def = next((f for f in provider.fields if f.name == field_name), None)
    if field_def and field_def.input_type == "checkbox":
        return raw_value.strip().lower() in ("1", "true", "yes", "on")
    return raw_value


def cmd_types(_args) -> int:
    for key, p in PROVIDER_TYPES.items():
        print(f"{key}  ({p.category}) — {p.label}")
        for f in p.fields:
            req = "requis" if f.required else "optionnel"
            print(f"    {f.name}  [{f.input_type}, {req}]  {f.help_text}")
    return 0


def cmd_list(_args) -> int:
    connections = list_connections()
    if not connections:
        print("Aucune connexion configurée.")
        return 0
    for c in connections:
        flags = []
        flags.append("activée" if c.enabled else "désactivée")
        if c.active:
            flags.append("★ active")
        print(f"{c.name}  type={c.type}  [{', '.join(flags)}]")
    return 0


def cmd_show(args) -> int:
    conn = get_connection(args.name)
    if conn is None:
        print(f"Connexion '{args.name}' introuvable.", file=sys.stderr)
        return 1
    print(f"name:    {conn.name}")
    print(f"type:    {conn.type}")
    print(f"enabled: {conn.enabled}")
    print(f"active:  {conn.active}")
    print("config:")
    for k, v in conn.config.items():
        provider = PROVIDER_TYPES.get(conn.type)
        field_def = next((f for f in provider.fields if f.name == k), None) if provider else None
        if field_def and field_def.input_type == "password" and v:
            v = "*" * 8
        print(f"    {k} = {v}")
    return 0


def cmd_set(args) -> int:
    existing = get_connection(args.name)
    provider_key = args.type or (existing.type if existing else None)
    if provider_key is None:
        print("--type est requis pour une nouvelle connexion.", file=sys.stderr)
        return 1
    if provider_key not in PROVIDER_TYPES:
        valid = ", ".join(PROVIDER_TYPES)
        print(f"Type inconnu '{provider_key}'. Types possibles : {valid}", file=sys.stderr)
        return 1

    config = dict(existing.config) if existing else {}
    for raw in args.field:
        if "=" not in raw:
            print(f"--field attend key=value, reçu '{raw}'", file=sys.stderr)
            return 1
        key, value = raw.split("=", 1)
        config[key] = _coerce_field(provider_key, key, value)

    enabled = existing.enabled if existing else True
    if args.enabled:
        enabled = True
    if args.disabled:
        enabled = False

    active = args.active or (existing.active if existing else False)

    try:
        save_connection(
            Connection(name=args.name, type=provider_key, enabled=enabled, active=active, config=config)
        )
    except InvalidConnectionName as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Connexion '{args.name}' ({provider_key}) enregistrée.")
    return 0


def cmd_delete(args) -> int:
    if delete_connection(args.name):
        print(f"Connexion '{args.name}' supprimée.")
        return 0
    print(f"Connexion '{args.name}' introuvable.", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    conn = sub.add_parser("connection", help="Gérer les connexions providers")
    conn_sub = conn.add_subparsers(dest="conn_command", required=True)

    p_types = conn_sub.add_parser("types", help="Lister les types de providers disponibles et leurs champs")
    p_types.set_defaults(func=cmd_types)

    p_list = conn_sub.add_parser("list", help="Lister les connexions configurées")
    p_list.set_defaults(func=cmd_list)

    p_show = conn_sub.add_parser("show", help="Afficher le détail d'une connexion")
    p_show.add_argument("name")
    p_show.set_defaults(func=cmd_show)

    p_set = conn_sub.add_parser(
        "set", help="Créer ou mettre à jour une connexion (voir 'connection types' pour les champs)"
    )
    p_set.add_argument("name")
    p_set.add_argument("--type", help="Type de provider (requis à la création)")
    p_set.add_argument("--field", action="append", default=[], metavar="key=value",
                        help="Champ de config, répétable (ex: --field api_url=https://...)")
    p_set.add_argument("--active", action="store_true",
                        help="En faire la connexion active de sa catégorie (désactive l'ancienne)")
    p_set.add_argument("--enabled", action="store_true")
    p_set.add_argument("--disabled", action="store_true")
    p_set.set_defaults(func=cmd_set)

    p_delete = conn_sub.add_parser("delete", help="Supprimer une connexion")
    p_delete.add_argument("name")
    p_delete.set_defaults(func=cmd_delete)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
