"""Mount third-party MCP servers as tool sources.

The ecosystem already ships MCP servers for GitHub, Slack, Notion, Stripe and
hundreds more. Rather than re-implementing those integrations, open-composio
proxies them: their tools appear in the registry like any other app, but their
credentials live in *your* vault, their calls pass through *your* policy and
audit log, and agents reach them through the same progressively-disclosed
meta-tools.

    open-composio mcp-add github -- npx -y @modelcontextprotocol/server-github
    open-composio connect github          # stores GITHUB_TOKEN in the keychain

Tool listings are cached on disk after the first discovery, so starting the
gateway doesn't spawn every upstream process. Refresh with
``open-composio mcp-refresh <name>``.
"""

import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.paths import data_dir
from .core.registry import AppDefinition, ToolRegistry
from .core.vault import BaseVault

CONFIG_FILE = "upstreams.json"
CACHE_FILE = "upstream_tools.json"


@dataclass
class UpstreamConfig:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    # Env vars the upstream needs (e.g. ["GITHUB_TOKEN"]); their values are
    # read from the vault at call time, never from this config file.
    env_keys: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _result_to_payload(result: Any) -> Any:
    """Flatten an MCP CallToolResult into plain JSON-able data."""
    contents = getattr(result, "content", None) or []
    texts = []
    for item in contents:
        text = getattr(item, "text", None)
        if text is not None:
            texts.append(text)
        else:  # image/blob/resource content
            texts.append(repr(item))
    if not texts:
        return {"content": []}
    joined = texts[0] if len(texts) == 1 else "\n".join(texts)
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, TypeError):
        return joined


class UpstreamManager:
    """Registers proxied MCP servers into a ToolRegistry."""

    def __init__(
        self,
        registry: ToolRegistry,
        vault: BaseVault,
        user_id: str = "default_user",
        directory: Optional[Path] = None,
    ):
        self.registry = registry
        self.vault = vault
        self.user_id = user_id
        self._dir = directory or data_dir()

    # ----------------------------------------------------------- persistence

    @property
    def _config_path(self) -> Path:
        return self._dir / CONFIG_FILE

    @property
    def _cache_path(self) -> Path:
        return self._dir / CACHE_FILE

    def _read_json(self, path: Path) -> Dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def list(self) -> Dict[str, UpstreamConfig]:
        return {
            name: UpstreamConfig(**cfg) for name, cfg in self._read_json(self._config_path).items()
        }

    def add(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env_keys: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        description: str = "",
    ) -> UpstreamConfig:
        if name in self.registry.apps and name not in self.list():
            raise ValueError(f"'{name}' collides with an existing app id.")
        cfg = UpstreamConfig(
            name=name,
            command=command,
            args=args or [],
            env_keys=env_keys or [],
            cwd=cwd,
            description=description or f"Proxied MCP server: {command}",
        )
        configs = self._read_json(self._config_path)
        configs[name] = cfg.to_dict()
        self._config_path.write_text(json.dumps(configs, indent=2), encoding="utf-8")
        return cfg

    def remove(self, name: str) -> None:
        configs = self._read_json(self._config_path)
        configs.pop(name, None)
        self._config_path.write_text(json.dumps(configs, indent=2), encoding="utf-8")
        cache = self._read_json(self._cache_path)
        cache.pop(name, None)
        self._cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        self.registry.remove_app(name)

    # -------------------------------------------------------------- sessions

    def _server_params(self, cfg: UpstreamConfig, auth_data: Optional[Dict[str, Any]]):
        from mcp import StdioServerParameters

        env = dict(os.environ)
        for key in cfg.env_keys:
            value = (auth_data or {}).get(key)
            if value:
                env[key] = str(value)
        return StdioServerParameters(
            command=cfg.command, args=cfg.args, env=env, cwd=cfg.cwd
        )

    async def _with_session(self, cfg: UpstreamConfig, auth_data, fn):
        """Spawn the upstream, run `fn(session)`, and tear it down.

        One process per call keeps lifetime management trivial; stdio servers
        start fast enough for interactive use. Pooling is a later optimization.
        """
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        params = self._server_params(cfg, auth_data)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)

    async def discover(self, name: str) -> List[Dict[str, Any]]:
        """Connect to an upstream, list its tools, and cache the result."""
        cfg = self.list().get(name)
        if cfg is None:
            raise KeyError(f"No upstream named '{name}'.")
        auth_data = self.vault.get(self.user_id, name)

        async def _list(session):
            listing = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or t.name,
                    "parameters_schema": t.inputSchema or {"type": "object", "properties": {}},
                }
                for t in listing.tools
            ]

        tools = await self._with_session(cfg, auth_data, _list)
        cache = self._read_json(self._cache_path)
        cache[name] = tools
        self._cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        self.register(cfg, tools)
        return tools

    # ------------------------------------------------------------ registration

    def _make_handler(self, cfg: UpstreamConfig, tool_name: str):
        async def handler(params: dict, auth_data: dict = None):
            async def _call(session):
                return await session.call_tool(tool_name, params or {})

            result = await self._with_session(cfg, auth_data, _call)
            if getattr(result, "isError", False):
                raise RuntimeError(
                    f"Upstream '{cfg.name}' reported an error: {_result_to_payload(result)}"
                )
            return _result_to_payload(result)

        return handler

    def register(self, cfg: UpstreamConfig, tools: List[Dict[str, Any]]) -> None:
        """Register an upstream's cached tools as an app in the registry."""
        needs_auth = bool(cfg.env_keys)
        self.registry.register_app(
            AppDefinition(
                id=cfg.name,
                name=cfg.name,
                description=cfg.description,
                auth_type="api_key" if needs_auth else "none",
                auth_config={
                    "fields": [
                        {
                            "name": key,
                            "label": key,
                            "type": "password",
                            "required": True,
                        }
                        for key in cfg.env_keys
                    ]
                }
                if needs_auth
                else {},
            )
        )
        for tool in tools:
            schema = dict(tool["parameters_schema"])
            schema.setdefault("description", tool["description"])
            self.registry.register_action(
                app_id=cfg.name,
                action_name=tool["name"],
                schema=schema,
                handler=self._make_handler(cfg, tool["name"]),
            )

    def load_all(self) -> None:
        """Register every configured upstream from its cached tool listing.

        Upstreams that have never been discovered are skipped (no process is
        spawned); run `discover()` / `open-composio mcp-refresh` for those.
        """
        cache = self._read_json(self._cache_path)
        for name, cfg in self.list().items():
            tools = cache.get(name)
            if tools:
                self.register(cfg, tools)

    def discover_sync(self, name: str) -> List[Dict[str, Any]]:
        return asyncio.run(self.discover(name))
