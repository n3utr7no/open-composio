"""CLI: `open-composio serve|mcp|apps|connect|disconnect`."""

import argparse
import getpass
import sys

from .sdk import OpenComposio


def cmd_serve(args) -> int:
    import uvicorn

    from .rest import create_app

    if args.host not in ("127.0.0.1", "localhost"):
        import os

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
    for app in oc.get_apps():
        status = "connected" if app["connected"] else "not connected"
        print(f"{app['id']:<15} {app['name']:<20} [{status}]  {app['description']}")
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
    return 0


def cmd_disconnect(args) -> int:
    oc = OpenComposio(user_id=args.user)
    oc.disconnect(args.app)
    print(f"Disconnected '{args.app}'.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="open-composio", description="Local-first MCP tool gateway.")
    parser.add_argument("--user", default="default_user", help="User id (default: default_user)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run the REST API + dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    p_mcp = sub.add_parser("mcp", help="Run the MCP server on stdio")
    p_mcp.set_defaults(func=cmd_mcp)

    p_apps = sub.add_parser("apps", help="List apps and connection status")
    p_apps.set_defaults(func=cmd_apps)

    p_connect = sub.add_parser("connect", help="Store credentials for an app")
    p_connect.add_argument("app")
    p_connect.add_argument(
        "--field", action="append", metavar="NAME=VALUE", help="Auth field (repeatable); prompts otherwise"
    )
    p_connect.set_defaults(func=cmd_connect)

    p_disconnect = sub.add_parser("disconnect", help="Remove stored credentials for an app")
    p_disconnect.add_argument("app")
    p_disconnect.set_defaults(func=cmd_disconnect)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
