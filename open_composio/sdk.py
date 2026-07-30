"""The OpenComposio SDK.

Embedded mode (default) runs the registry, vault and executor **in-process** —
no server, no port, nothing leaves the machine:

    from open_composio import OpenComposio

    oc = OpenComposio()
    oc.connect("github", token="ghp_...")          # stored in the OS keychain
    tools = oc.get_tools("github", "web_search")
    tools.as_openai()                              # OpenAI function definitions
    tools.as_anthropic()                           # Anthropic tool definitions
    result = tools.call("github_get_user", {})

Remote mode talks to a shared local server over REST with the same API:

    oc = OpenComposio(base_url="http://127.0.0.1:8000")
"""

import inspect
import json
from typing import Any, Callable, Dict, List, Optional

from .core.executor import Executor
from .core.registry import AppDefinition, ToolRegistry
from .core.vault import BaseVault, default_vault

LOCAL_APP_ID = "local"

_PY_TO_JSON_TYPE = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _schema_from_signature(fn: Callable, description: str) -> Dict[str, Any]:
    sig = inspect.signature(fn)
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for name, param in sig.parameters.items():
        if name in ("self", "auth_data"):
            continue
        json_type = _PY_TO_JSON_TYPE.get(param.annotation, "string")
        properties[name] = {"type": json_type, "description": name.replace("_", " ")}
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "description": description,
        "type": "object",
        "properties": properties,
        "required": required,
    }


class Tool:
    """A single action bound to a client, renderable in multiple tool formats."""

    def __init__(self, full_name: str, description: str, schema: Dict[str, Any], invoker: Callable):
        self.name = full_name
        self.description = description
        self.schema = schema
        self._invoke = invoker

    def as_openai(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    def as_anthropic(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema,
        }

    def __call__(self, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._invoke(self.name, params or {})

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"


class ToolCollection(list):
    def as_openai(self) -> List[Dict[str, Any]]:
        return [t.as_openai() for t in self]

    def as_anthropic(self) -> List[Dict[str, Any]]:
        return [t.as_anthropic() for t in self]

    def call(self, name: str, arguments: Any) -> Any:
        """Dispatch a tool call by name. `arguments` may be a dict or a JSON
        string (as returned in LLM tool-call payloads)."""
        if isinstance(arguments, str):
            arguments = json.loads(arguments) if arguments.strip() else {}
        for tool in self:
            if tool.name == name:
                return tool(arguments)
        raise KeyError(f"Tool '{name}' is not in this collection.")


class OpenComposio:
    def __init__(
        self,
        base_url: Optional[str] = None,
        user_id: str = "default_user",
        vault: Optional[BaseVault] = None,
        load_builtin: bool = True,
        audit: bool = True,
    ):
        self.user_id = user_id
        self._remote = base_url is not None

        if self._remote:
            import httpx

            self._base_url = base_url.rstrip("/")
            self._http = httpx.Client()
            self.registry = None
            self.vault = None
            self.executor = None
        else:
            self.registry = ToolRegistry()
            self.vault = vault or default_vault()
            self.executor = Executor(self.registry, self.vault, audit=audit)
            if load_builtin:
                from .apps import load_builtin_apps

                load_builtin_apps(self.registry)

    # ------------------------------------------------------------------ apps

    def get_apps(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        user_id = user_id or self.user_id
        if self._remote:
            resp = self._http.get(f"{self._base_url}/api/apps", params={"user_id": user_id})
            resp.raise_for_status()
            return resp.json()["apps"]

        connected = set(self.vault.list_apps(user_id))
        return [
            {
                "id": app.id,
                "name": app.name,
                "description": app.description,
                "auth_type": app.auth_type,
                "auth_config": app.auth_config,
                "requires_auth": app.auth_type != "none",
                "connected": app.id in connected or app.auth_type == "none",
            }
            for app in self.registry.apps.values()
        ]

    def get_actions(self, app_id: str) -> List[Dict[str, Any]]:
        if self._remote:
            resp = self._http.get(f"{self._base_url}/api/apps/{app_id}/actions")
            resp.raise_for_status()
            return resp.json()["actions"]

        if app_id not in self.registry.apps:
            raise KeyError(f"App '{app_id}' not found.")
        return [
            {
                "name": a.name,
                "description": a.description,
                "parameters_schema": a.parameters_schema,
            }
            for a in self.registry.apps[app_id].actions.values()
        ]

    # ----------------------------------------------------------- connections

    def connect(self, app_id: str, user_id: Optional[str] = None, **auth_data: Any) -> None:
        """Store credentials for an app. Embedded mode writes to the local
        vault (OS keychain by default); remote mode POSTs to the local server.

        `user_id` scopes the connection (defaults to this client's user); an
        auth field literally named "user_id" must be passed via a dict:
        ``oc.connect("app", **{"user_id": ...})`` is NOT supported for that case."""
        user_id = user_id or self.user_id
        if self._remote:
            resp = self._http.post(
                f"{self._base_url}/api/connections/{app_id}",
                params={"user_id": user_id},
                json=auth_data,
            )
            resp.raise_for_status()
            return

        if app_id not in self.registry.apps:
            raise KeyError(f"App '{app_id}' not found.")
        app = self.registry.apps[app_id]
        fields = [f["name"] for f in app.auth_config.get("fields", []) if f.get("required")]
        missing = [f for f in fields if f not in auth_data]
        if missing:
            raise ValueError(f"Missing required auth fields for '{app_id}': {', '.join(missing)}")
        self.vault.set(user_id, app_id, auth_data)

    def disconnect(self, app_id: str, user_id: Optional[str] = None) -> None:
        user_id = user_id or self.user_id
        if self._remote:
            resp = self._http.delete(
                f"{self._base_url}/api/connections/{app_id}", params={"user_id": user_id}
            )
            resp.raise_for_status()
            return
        self.vault.delete(user_id, app_id)

    # -------------------------------------------------------------- execution

    def execute(
        self,
        app_id: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> Any:
        params = params or {}
        user_id = user_id or self.user_id
        if self._remote:
            resp = self._http.post(
                f"{self._base_url}/api/execute/{app_id}/{action}",
                json={"user_id": user_id, "params": params},
            )
            resp.raise_for_status()
            return resp.json().get("result")
        return self.executor.execute(app_id, action, params, user_id)

    async def aexecute(
        self,
        app_id: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> Any:
        params = params or {}
        if self._remote:
            raise RuntimeError("aexecute() is only available in embedded mode.")
        return await self.executor.aexecute(app_id, action, params, user_id or self.user_id)

    # ------------------------------------------------------------------ tools

    def get_tools(self, *app_ids: str) -> ToolCollection:
        """Bound tools for the given apps (all apps if none given)."""
        wanted = list(app_ids) if app_ids else [a["id"] for a in self.get_apps()]
        tools = ToolCollection()
        for app_id in wanted:
            for action in self.get_actions(app_id):
                full_name = f"{app_id}_{action['name']}"
                tools.append(
                    Tool(
                        full_name=full_name,
                        description=action["description"],
                        schema=action["parameters_schema"],
                        invoker=self._make_invoker(app_id, action["name"]),
                    )
                )
        return tools

    def _make_invoker(self, app_id: str, action: str) -> Callable:
        def invoke(_full_name: str, params: Dict[str, Any]) -> Any:
            return self.execute(app_id, action, params)

        return invoke

    def tool(
        self,
        fn: Optional[Callable] = None,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ):
        """Decorator: register a local Python function as a tool.

            @oc.tool
            def add(a: int, b: int) -> int:
                \"\"\"Add two numbers.\"\"\"
                return a + b

        The function flows through the same executor (middleware, audit) as
        catalog apps, under the app id ``local``.
        """
        if self._remote:
            raise RuntimeError("@oc.tool requires embedded mode (no base_url).")

        def decorate(func: Callable) -> Callable:
            tool_name = name or func.__name__
            desc = description or (inspect.getdoc(func) or tool_name).strip().split("\n")[0]
            schema = _schema_from_signature(func, desc)

            if LOCAL_APP_ID not in self.registry.apps:
                self.registry.register_app(
                    AppDefinition(
                        id=LOCAL_APP_ID,
                        name="Local Tools",
                        description="Custom Python functions registered via @oc.tool",
                        auth_type="none",
                    )
                )

            if inspect.iscoroutinefunction(func):
                async def handler(params: dict, auth_data: dict = None):
                    return await func(**params)
            else:
                async def handler(params: dict, auth_data: dict = None):
                    return func(**params)

            self.registry.register_action(LOCAL_APP_ID, tool_name, schema, handler)
            return func

        return decorate(fn) if fn is not None else decorate

    # ------------------------------------------------------------- middleware

    def use(
        self,
        before: Optional[Callable] = None,
        after: Optional[Callable] = None,
    ) -> None:
        """Register execution middleware (embedded mode). See Executor.use."""
        if self._remote:
            raise RuntimeError("Middleware requires embedded mode (no base_url).")
        self.executor.use(before=before, after=after)

    # ------------------------------------------------------------------- MCP

    def serve_mcp(self, transport: str = "stdio") -> None:
        """Expose this instance as an MCP server (blocks)."""
        if self._remote:
            raise RuntimeError("serve_mcp() requires embedded mode (no base_url).")
        from .mcp_server import build_mcp_server

        build_mcp_server(self).run(transport=transport)
