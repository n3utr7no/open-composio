"""CLI: serve, mcp, apps, connect/disconnect, policy, mcp-add/list/refresh/remove."""

import argparse
import asyncio
import getpass
import json
import sys

from .core.policy import Policy
from .sdk import OpenComposio


def cmd_serve(args) -> int:
    import os

    import uvicorn

    from .rest import create_app

    if args.host not in ("127.0.0.1", "localhost"):
        if not os.environ.get("OPEN_COMPOSIO_API_TOKEN"):
            print(
                "Refusing to bind beyond localhost without auth. "
                "Set OPEN_COMPOSIO_API_TOKEN and retry.",
                file=sys.stderr,
            )
            return 1
    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def cmd_mcp(args) -> int:
    OpenComposio(user_id=args.user).serve_mcp(transport="stdio")
    return 0


def cmd_apps(args) -> int:
    oc = OpenComposio(user_id=args.user)
    print(f"vault backend: {oc.vault.name}")
    policy = oc.policy
    print(f"policy: {'enforced' if policy else 'none (permissive)'}\n")
    for app in oc.get_apps():
        if not app["requires_auth"]:
            status = "no auth needed"
        else:
            status = "connected" if app["connected"] else "not connected"
        n_actions = len(oc.registry.apps[app["id"]].actions)
        print(
            f"{app['id']:<15} {app['name']:<20} [{status}]  "
            f"{n_actions} action(s)  {app['description']}"
        )
    return 0


def cmd_connect(args) -> int:
    oc = OpenComposio(user_id=args.user)
    if args.app not in oc.registry.apps:
        print(f"Unknown app '{args.app}'. Run `open-composio apps` to list.", file=sys.stderr)
        return 1
    app = oc.registry.apps[args.app]
    if app.auth_type == "none":
        print(f"'{args.app}' needs no authentication.")
        return 0

    auth_data = dict(kv.split("=", 1) for kv in args.field or [])
    for field in app.auth_config.get("fields", []):
        if field["name"] in auth_data:
            continue
        prompt = f"{field.get('label', field['name'])}: "
        value = getpass.getpass(prompt) if field.get("type") == "password" else input(prompt)
        if value:
            auth_data[field["name"]] = value

    oc.connect(args.app, **auth_data)
    print(f"Connected '{args.app}'. Credentials stored in vault: {oc.vault.name}.")

    if args.verify:
        outcome = asyncio.run(oc.verify(args.app))
        if outcome["ok"] is None:
            print(f"Verification skipped: {outcome['detail']}")
        elif outcome["ok"]:
            print(f"Verified: {outcome['detail']}")
        else:
            print(f"Verification FAILED: {outcome['detail']}", file=sys.stderr)
            return 1
    return 0


def cmd_disconnect(args) -> int:
    oc = OpenComposio(user_id=args.user)
    oc.disconnect(args.app)
    print(f"Disconnected '{args.app}'.")
    return 0


def cmd_audit(args) -> int:
    oc = OpenComposio(user_id=args.user)
    records = oc.executor.read_audit(limit=args.limit)
    if not records:
        print("No audit records yet.")
        return 0
    import datetime

    for r in records:
        ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts}  {r['app_id']}.{r['action']:<20} {r['status']:<10} {r['params_sha256_16']}"
        if r.get("error"):
            line += f"  {r['error'][:60]}"
        print(line)
    return 0


# ------------------------------------------------------------------- policy


def cmd_policy(args) -> int:
    policy = Policy.load() or Policy()
    changed = False

    if args.policy_command == "show":
        print(json.dumps(
            {
                "allow": policy.allow,
                "deny": policy.deny,
                "approved": policy.approved,
                "require_approval": policy.require_approval,
            },
            indent=2,
        ))
        print(f"\nfile: {Policy.path()}"
              f"{'' if Policy.load() else ' (does not exist — permissive)'}")
        return 0

    target = args.pattern
    if args.policy_command == "allow":
        policy.allow.append(target)
        changed = True
    elif args.policy_command == "deny":
        policy.deny.append(target)
        changed = True
    elif args.policy_command == "approve":
        policy.approved.append(target)
        changed = True

    if changed:
        policy.save()
        print(f"Policy updated ({args.policy_command}: {target}) -> {Policy.path()}")
    return 0


# ----------------------------------------------------------------- upstreams


def cmd_mcp_add(args) -> int:
    if not args.command_parts:
        print("Provide the upstream command after `--`.", file=sys.stderr)
        return 1
    oc = OpenComposio(user_id=args.user)
    command, *cmd_args = args.command_parts
    cfg = oc.upstreams.add(
        name=args.name,
        command=command,
        args=cmd_args,
        env_keys=args.env or [],
        description=args.description or "",
    )
    print(f"Added upstream '{cfg.name}': {cfg.command} {' '.join(cfg.args)}")
    if cfg.env_keys:
        print(f"Run `open-composio connect {cfg.name}` to store: {', '.join(cfg.env_keys)}")
    print(f"Then `open-composio mcp-refresh {cfg.name}` to discover its tools.")
    return 0


def cmd_mcp_list(args) -> int:
    oc = OpenComposio(user_id=args.user)
    upstreams = oc.upstreams.list()
    if not upstreams:
        print("No upstream MCP servers configured. Add one with `open-composio mcp-add`.")
        return 0
    for name, cfg in upstreams.items():
        n = len(oc.registry.apps[name].actions) if name in oc.registry.apps else 0
        discovered = f"{n} tool(s)" if n else "not yet discovered"
        print(f"{name:<15} {cfg.command} {' '.join(cfg.args):<40} [{discovered}]")
    return 0


def cmd_mcp_refresh(args) -> int:
    oc = OpenComposio(user_id=args.user)
    try:
        tools = oc.upstreams.discover_sync(args.name)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to reach upstream '{args.name}': {exc}", file=sys.stderr)
        return 1
    print(f"Discovered {len(tools)} tool(s) from '{args.name}':")
    for t in tools:
        print(f"  {t['name']}: {t['description'][:70]}")
    return 0


def cmd_mcp_remove(args) -> int:
    oc = OpenComposio(user_id=args.user)
    oc.upstreams.remove(args.name)
    print(f"Removed upstream '{args.name}'.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="open-composio", description="Local-first MCP tool gateway."
    )
    parser.add_argument("--user", default="default_user", help="User id (default: default_user)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="Run the REST API + dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("mcp", help="Run the MCP server on stdio")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("apps", help="List apps and connection status")
    p.set_defaults(func=cmd_apps)

    p = sub.add_parser("connect", help="Store credentials for an app")
    p.add_argument("app")
    p.add_argument(
        "--field",
        action="append",
        metavar="NAME=VALUE",
        help="Auth field (repeatable); prompts securely otherwise",
    )
    p.add_argument(
        "--verify", action="store_true", help="Smoke-test the credentials after storing"
    )
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("disconnect", help="Remove stored credentials for an app")
    p.add_argument("app")
    p.set_defaults(func=cmd_disconnect)

    p = sub.add_parser("audit", help="Show recent tool executions")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("policy", help="Inspect or edit the permission policy")
    psub = p.add_subparsers(dest="policy_command", required=True)
    psub.add_parser("show", help="Print the active policy")
    for name, helptext in [
        ("allow", "Allowlist a tool pattern (e.g. github.*)"),
        ("deny", "Block a tool pattern"),
        ("approve", "Pre-approve a destructive tool pattern"),
    ]:
        sp = psub.add_parser(name, help=helptext)
        sp.add_argument("pattern", help="app.action pattern, wildcards allowed")
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser(
        "mcp-add",
        help="Mount a third-party MCP server as a tool source",
        epilog="example: open-composio mcp-add github --env GITHUB_TOKEN "
        "-- npx -y @modelcontextprotocol/server-github",
    )
    p.add_argument("name")
    p.add_argument("--env", action="append", help="Env var the upstream needs (repeatable)")
    p.add_argument("--description", default="")
    p.add_argument("command_parts", nargs=argparse.REMAINDER, help="-- command args...")
    p.set_defaults(func=cmd_mcp_add)

    p = sub.add_parser("mcp-list", help="List mounted MCP servers")
    p.set_defaults(func=cmd_mcp_list)

    p = sub.add_parser("mcp-refresh", help="Re-discover an upstream's tools")
    p.add_argument("name")
    p.set_defaults(func=cmd_mcp_refresh)

    p = sub.add_parser("mcp-remove", help="Unmount an MCP server")
    p.add_argument("name")
    p.set_defaults(func=cmd_mcp_remove)

    args = parser.parse_args(argv)
    if getattr(args, "command_parts", None) and args.command_parts[0] == "--":
        args.command_parts = args.command_parts[1:]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
