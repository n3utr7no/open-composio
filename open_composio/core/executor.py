"""Execution pipeline: auth resolution -> before hooks -> handler -> after hooks -> audit.

All facades (SDK, MCP, REST) route through Executor, so middleware and the
audit log see every call regardless of transport.
"""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .registry import ToolRegistry
from .vault import BaseVault
from .paths import data_dir


class NotConnectedError(Exception):
    def __init__(self, app_id: str):
        self.app_id = app_id
        super().__init__(
            f"App '{app_id}' requires authentication. Connect it first "
            f"(dashboard, `open-composio connect {app_id}`, or oc.connect())."
        )


class PermissionDenied(Exception):
    pass


@dataclass
class ExecutionContext:
    user_id: str
    app_id: str
    action: str
    params: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


def _params_hash(params: Dict[str, Any]) -> str:
    try:
        canonical = json.dumps(params, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = repr(params)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class Executor:
    def __init__(
        self,
        registry: ToolRegistry,
        vault: BaseVault,
        audit_path: Optional[Path] = None,
        audit: bool = True,
    ):
        self.registry = registry
        self.vault = vault
        self._before: List[Callable[[ExecutionContext], Any]] = []
        self._after: List[Callable[[ExecutionContext, Any], Any]] = []
        self._audit_path = audit_path or (data_dir() / "audit.jsonl")
        self._audit_enabled = audit

    def use(
        self,
        before: Optional[Callable[[ExecutionContext], Any]] = None,
        after: Optional[Callable[[ExecutionContext, Any], Any]] = None,
    ) -> None:
        """Register middleware. `before` may raise (e.g. PermissionDenied) to
        block the call; `after` observes the result."""
        if before:
            self._before.append(before)
        if after:
            self._after.append(after)

    def _audit(self, ctx: ExecutionContext, status: str, error: Optional[str] = None) -> None:
        if not self._audit_enabled:
            return
        record = {
            "ts": time.time(),
            "user_id": ctx.user_id,
            "app_id": ctx.app_id,
            "action": ctx.action,
            "params_sha256_16": _params_hash(ctx.params),
            "status": status,
        }
        if error:
            record["error"] = error[:500]
        try:
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass  # auditing must never take down execution

    async def aexecute(
        self,
        app_id: str,
        action: str,
        params: Dict[str, Any],
        user_id: str = "default_user",
    ) -> Any:
        if app_id not in self.registry.apps:
            raise ValueError(f"App '{app_id}' not found.")
        app = self.registry.apps[app_id]
        if action not in app.actions:
            raise ValueError(f"Action '{action}' not found on app '{app_id}'.")

        auth_data = None
        if app.auth_type != "none":
            auth_data = self.vault.get(user_id, app_id)
            if not auth_data:
                raise NotConnectedError(app_id)

        ctx = ExecutionContext(user_id=user_id, app_id=app_id, action=action, params=params)
        try:
            for hook in self._before:
                maybe = hook(ctx)
                if asyncio.iscoroutine(maybe):
                    await maybe
        except Exception as exc:
            self._audit(ctx, "denied", str(exc))
            raise

        try:
            result = await self.registry.execute_action(app_id, action, params, auth_data)
        except Exception as exc:
            self._audit(ctx, "error", str(exc))
            raise

        for hook in self._after:
            maybe = hook(ctx, result)
            if asyncio.iscoroutine(maybe):
                await maybe

        self._audit(ctx, "ok")
        return result

    def execute(
        self,
        app_id: str,
        action: str,
        params: Dict[str, Any],
        user_id: str = "default_user",
    ) -> Any:
        """Sync wrapper for non-async callers (scripts, REPLs)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aexecute(app_id, action, params, user_id))
        raise RuntimeError(
            "Executor.execute() called from a running event loop; use `await aexecute(...)`."
        )
