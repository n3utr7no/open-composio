"""Permission policy: which tools an agent may call, and which need a human.

A policy is enforced inside the executor, so it applies identically whether the
call arrives over MCP, REST, or the embedded SDK.

Rules, in precedence order:

1. `deny` — an explicit block, always wins.
2. `allow` — if non-empty, acts as an allowlist: anything not listed is denied.
3. destructive actions — require either an entry in `approved` or an
   `approval_handler` that returns True for this specific call.

Patterns are `app_id.action` with `*` wildcards: ``github.*``,
``*.create_issue``, ``github.create_issue``.

No policy file and no explicit policy means permissive (everything allowed) —
the enforcement only appears once someone opts in, so upgrading never silently
breaks a working setup.
"""

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .paths import data_dir

# Actions whose schema doesn't mark them explicitly but which mutate remote
# state; used as a fallback heuristic so a new integration is safe by default.
_DESTRUCTIVE_VERBS = (
    "create",
    "delete",
    "remove",
    "update",
    "write",
    "send",
    "post",
    "merge",
    "close",
    "archive",
    "revoke",
    "cancel",
    "publish",
    "deploy",
)


class PolicyDenied(Exception):
    """Raised when a policy rule blocks a call."""


class ApprovalRequired(Exception):
    """Raised when a destructive call needs human sign-off it didn't get."""


def is_destructive(action_name: str, schema: Optional[Dict[str, Any]] = None) -> bool:
    """Whether an action mutates remote state.

    An explicit ``x-destructive`` in the schema always wins; otherwise fall
    back to a verb heuristic so unmarked third-party tools (e.g. proxied MCP
    servers) don't default to "safe".
    """
    if schema is not None and "x-destructive" in schema:
        return bool(schema["x-destructive"])
    leaf = action_name.split(".")[-1].lower()
    return any(leaf.startswith(verb) or f"_{verb}" in leaf for verb in _DESTRUCTIVE_VERBS)


@dataclass
class Policy:
    allow: List[str] = field(default_factory=list)
    deny: List[str] = field(default_factory=list)
    approved: List[str] = field(default_factory=list)
    # Called as handler(app_id, action, params) -> bool for destructive calls
    # not already in `approved`. None means "no way to ask" -> deny.
    approval_handler: Optional[Callable[[str, str, Dict[str, Any]], bool]] = None
    require_approval: bool = True

    @staticmethod
    def _matches(patterns: List[str], target: str) -> bool:
        return any(fnmatch.fnmatch(target, p) for p in patterns)

    def check(
        self,
        app_id: str,
        action: str,
        params: Dict[str, Any],
        schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Raise PolicyDenied / ApprovalRequired if this call is not permitted."""
        target = f"{app_id}.{action}"

        if self._matches(self.deny, target):
            raise PolicyDenied(f"'{target}' is denied by policy.")

        if self.allow and not self._matches(self.allow, target):
            raise PolicyDenied(
                f"'{target}' is not in the policy allowlist. "
                f"Add it with: open-composio policy allow {target}"
            )

        if not self.require_approval or not is_destructive(action, schema):
            return
        if self._matches(self.approved, target):
            return

        if self.approval_handler is None:
            raise ApprovalRequired(
                f"'{target}' modifies remote state and needs approval. "
                f"Pre-approve it with: open-composio policy approve {target}"
            )
        if not self.approval_handler(app_id, action, params):
            raise ApprovalRequired(f"Approval for '{target}' was declined.")

    # ------------------------------------------------------------ persistence

    @classmethod
    def path(cls) -> Path:
        return data_dir() / "policy.json"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Optional["Policy"]:
        """Load the policy file, or None if there isn't one (permissive)."""
        path = path or cls.path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return cls(
            allow=raw.get("allow", []),
            deny=raw.get("deny", []),
            approved=raw.get("approved", []),
            require_approval=raw.get("require_approval", True),
        )

    def save(self, path: Optional[Path] = None) -> None:
        path = path or self.path()
        path.write_text(
            json.dumps(
                {
                    "allow": self.allow,
                    "deny": self.deny,
                    "approved": self.approved,
                    "require_approval": self.require_approval,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
