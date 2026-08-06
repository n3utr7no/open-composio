"""Execution pipeline. Every facade (SDK, MCP, REST) routes through Executor,
so validation, policy, middleware, caching and the audit log see every call
regardless of transport.

Order of operations:

    resolve auth -> validate params -> policy check -> before hooks
    -> cache lookup -> handler (with timeout) -> after hooks -> truncate
    -> audit
"""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .paths import data_dir
from .policy import ApprovalRequired, Policy, PolicyDenied
from .registry import ToolRegistry
from .vault import BaseVault

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RESULT_BYTES = 100_000


class NotConnectedError(Exception):
    def __init__(self, app_id: str):
        self.app_id = app_id
        super().__init__(
            f"App '{app_id}' requires authentication. Connect it first "
            f"(dashboard, `open-composio connect {app_id}`, or oc.connect())."
        )


class ValidationError(Exception):
    """Params did not match the action's JSON Schema."""


class PermissionDenied(Exception):
    """Kept for backwards compatibility; prefer policy.PolicyDenied."""


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


def validate_params(params: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """Validate params against an action's JSON Schema.

    Produces messages a model can act on ("unexpected property 'x'; expected
    'y'") rather than a raw Python TypeError from the handler. Falls back to a
    minimal check if `jsonschema` isn't installed.
    """
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    try:
        import jsonschema
    except ImportError:
        missing = [k for k in required if k not in params]
        if missing:
            raise ValidationError(
                f"Missing required parameter(s): {', '.join(missing)}. "
                f"Expected: {', '.join(properties) or '(none)'}"
            )
        unexpected = [k for k in params if properties and k not in properties]
        if unexpected:
            raise ValidationError(
                f"Unexpected parameter(s): {', '.join(unexpected)}. "
                f"Expected: {', '.join(properties) or '(none)'}"
            )
        return

    validator_schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        # Reject unknown params by default (catches hallucinated field names),
        # but let a schema opt in to freeform input explicitly.
        "additionalProperties": schema.get("additionalProperties", False),
    }
    validator = jsonschema.Draft7Validator(validator_schema)
    errors = sorted(validator.iter_errors(params), key=lambda e: list(e.path))
    if not errors:
        return
    detail = "; ".join(
        f"{'.'.join(str(p) for p in e.path) or 'params'}: {e.message}" for e in errors[:5]
    )
    raise ValidationError(
        f"{detail}. Expected properties: {', '.join(properties) or '(none)'}"
    )


def truncate_result(result: Any, max_bytes: int) -> Any:
    """Cap a serialized result so one large API response can't blow an agent's
    context window. Returns a marker object explaining the truncation."""
    if max_bytes <= 0:
        return result
    try:
        encoded = json.dumps(result, default=str)
    except (TypeError, ValueError):
        return result
    if len(encoded) <= max_bytes:
        return result
    return {
        "truncated": True,
        "original_bytes": len(encoded),
        "max_bytes": max_bytes,
        "hint": "Result was too large. Narrow the query or request fewer items.",
        "preview": encoded[:max_bytes],
    }


class Executor:
    def __init__(
        self,
        registry: ToolRegistry,
        vault: BaseVault,
        audit_path: Optional[Path] = None,
        audit: bool = True,
        policy: Optional[Policy] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        validate: bool = True,
    ):
        self.registry = registry
        self.vault = vault
        self.policy = policy
        self.timeout = timeout
        self.max_result_bytes = max_result_bytes
        self.validate = validate
        self._before: List[Callable[[ExecutionContext], Any]] = []
        self._after: List[Callable[[ExecutionContext, Any], Any]] = []
        self._audit_path = audit_path or (data_dir() / "audit.jsonl")
        self._audit_enabled = audit
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def use(
        self,
        before: Optional[Callable[[ExecutionContext], Any]] = None,
        after: Optional[Callable[[ExecutionContext, Any], Any]] = None,
    ) -> None:
        """Register execution middleware.

        `before` may raise (e.g. PolicyDenied) to block the call, and may
        mutate `ctx.params` in place. `after` observes the result and, if it
        returns a non-None value, *replaces* it — which is how redaction or
        reshaping is done.
        """
        if before:
            self._before.append(before)
        if after:
            self._after.append(after)

    # --------------------------------------------------------------- auditing

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

    def read_audit(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Most recent audit records, newest first."""
        try:
            with open(self._audit_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        records = []
        for line in reversed(lines[-limit:]):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    # ---------------------------------------------------------------- caching

    def _cache_key(self, user_id: str, app_id: str, action: str, params: Dict[str, Any]) -> str:
        return f"{user_id}:{app_id}.{action}:{_params_hash(params)}"

    def _cache_get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._cache[key]
            return None
        return value

    def invalidate_cache(self) -> None:
        self._cache.clear()

    # -------------------------------------------------------------- execution

    async def aexecute(
        self,
        app_id: str,
        action: str,
        params: Dict[str, Any],
        user_id: str = "default_user",
        timeout: Optional[float] = None,
    ) -> Any:
        if app_id not in self.registry.apps:
            raise ValueError(f"App '{app_id}' not found.")
        app = self.registry.apps[app_id]
        if action not in app.actions:
            raise ValueError(f"Action '{action}' not found on app '{app_id}'.")
        action_def = app.actions[action]
        schema = action_def.parameters_schema

        auth_data = None
        if app.auth_type != "none":
            auth_data = self.vault.get(user_id, app_id)
            if not auth_data:
                raise NotConnectedError(app_id)

        ctx = ExecutionContext(user_id=user_id, app_id=app_id, action=action, params=params)

        if self.validate:
            try:
                validate_params(params, schema)
            except ValidationError as exc:
                self._audit(ctx, "invalid", str(exc))
                raise

        if self.policy is not None:
            try:
                self.policy.check(app_id, action, params, schema)
            except (PolicyDenied, ApprovalRequired) as exc:
                self._audit(ctx, "denied", str(exc))
                raise

        try:
            for hook in self._before:
                maybe = hook(ctx)
                if asyncio.iscoroutine(maybe):
                    await maybe
        except Exception as exc:
            self._audit(ctx, "denied", str(exc))
            raise

        cache_ttl = schema.get("x-cache-ttl", 0)
        cache_key = None
        if cache_ttl:
            cache_key = self._cache_key(user_id, app_id, action, ctx.params)
            cached = self._cache_get(cache_key)
            if cached is not None:
                self._audit(ctx, "cache_hit")
                return cached

        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            coro = self.registry.execute_action(app_id, action, ctx.params, auth_data)
            if effective_timeout and effective_timeout > 0:
                result = await asyncio.wait_for(coro, timeout=effective_timeout)
            else:
                result = await coro
        except asyncio.TimeoutError:
            msg = f"'{app_id}.{action}' timed out after {effective_timeout}s."
            self._audit(ctx, "timeout", msg)
            raise TimeoutError(msg)
        except Exception as exc:
            self._audit(ctx, "error", str(exc))
            raise

        for hook in self._after:
            maybe = hook(ctx, result)
            if asyncio.iscoroutine(maybe):
                maybe = await maybe
            if maybe is not None:
                result = maybe  # after hooks may replace the result (redaction)

        result = truncate_result(result, self.max_result_bytes)

        if cache_key:
            self._cache[cache_key] = (time.monotonic() + cache_ttl, result)

        self._audit(ctx, "ok")
        return result

    def execute(
        self,
        app_id: str,
        action: str,
        params: Dict[str, Any],
        user_id: str = "default_user",
        timeout: Optional[float] = None,
    ) -> Any:
        """Sync wrapper for non-async callers (scripts, REPLs).

        Inside a running event loop this raises — use `await aexecute(...)`,
        or the SDK's `await tools.acall(...)`.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aexecute(app_id, action, params, user_id, timeout))
        raise RuntimeError(
            "Executor.execute() called from a running event loop; "
            "use `await aexecute(...)` (or `await tools.acall(...)`) instead."
        )
