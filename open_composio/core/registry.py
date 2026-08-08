import difflib
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


# Filler words carry no signal but do real damage: a bare "a" substring-matches
# any tool whose name merely contains the letter, which is enough to outrank a
# genuine hit on a large catalog.
_STOPWORDS = frozenset(
    {"the", "and", "for", "from", "with", "that", "this", "please", "all", "any"}
)


def _tokenize(query: str) -> List[str]:
    tokens = [t.strip(".,!?;:'\"()") for t in query.lower().split()]
    tokens = [t for t in tokens if t]
    meaningful = [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]
    # An all-stopword query ("in a") is better served by weak matches than none.
    return meaningful or tokens


class ActionDefinition(BaseModel):
    name: str
    description: str
    parameters_schema: Dict[str, Any]  # JSON Schema of input parameters


class AppDefinition(BaseModel):
    id: str
    name: str
    description: str
    auth_type: str = "none"  # "none", "api_key", "oauth2"
    auth_config: Dict[str, Any] = Field(default_factory=dict)
    actions: Dict[str, ActionDefinition] = Field(default_factory=dict)


class ToolRegistry:
    """In-memory registry of apps and their action handlers.

    Action handlers are async callables with signature
    ``handler(params: dict, auth_data: dict | None) -> Any``.
    """

    def __init__(self):
        self.apps: Dict[str, AppDefinition] = {}
        self._action_handlers: Dict[str, Callable] = {}
        # full action name -> (app_id, action_name); app ids may themselves
        # contain underscores, so the full name cannot be split reliably
        self._full_names: Dict[str, Tuple[str, str]] = {}

    def register_app(self, app: AppDefinition):
        self.apps[app.id] = app

    def register_action(self, app_id: str, action_name: str, schema: Dict[str, Any], handler: Callable):
        if app_id not in self.apps:
            raise ValueError(f"App {app_id} is not registered.")

        full_name = f"{app_id}_{action_name}"
        self.apps[app_id].actions[action_name] = ActionDefinition(
            name=action_name,
            description=schema.get("description", ""),
            parameters_schema=schema,
        )
        self._action_handlers[full_name] = handler
        self._full_names[full_name] = (app_id, action_name)

    def remove_app(self, app_id: str) -> None:
        """Drop an app and every action registered under it."""
        self.apps.pop(app_id, None)
        for full_name, (owner, _) in list(self._full_names.items()):
            if owner == app_id:
                del self._full_names[full_name]
                self._action_handlers.pop(full_name, None)

    def resolve(self, full_name: str) -> Tuple[str, str]:
        """Map a full tool name like ``github_create_issue`` back to (app_id, action)."""
        if full_name not in self._full_names:
            raise KeyError(f"Tool '{full_name}' not found.")
        return self._full_names[full_name]

    def get_action(self, app_id: str, action_name: str) -> ActionDefinition:
        return self.apps[app_id].actions[action_name]

    def iter_actions(self):
        """Yield (full_name, app_id, action) for every registered action."""
        for full_name, (app_id, action_name) in self._full_names.items():
            yield full_name, app_id, self.apps[app_id].actions[action_name]

    def search(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Rank actions against a free-text query. Returns dicts with
        full tool name, description and app id, best match first."""
        tokens = _tokenize(query)
        scored = []
        for full_name, app_id, action in self.iter_actions():
            name_l = full_name.lower()
            name_words = set(name_l.split("_"))
            desc_l = action.description.lower()
            score = 0.0
            for tok in tokens:
                if tok == name_l:
                    score += 4
                elif tok in name_words:
                    # A whole word in the tool name. Scored above a bare
                    # substring so "star" prefers star_repo over unstar_repo.
                    score += 3
                elif tok in name_l:
                    score += 1.5
                if tok in desc_l:
                    score += 1
            score += difflib.SequenceMatcher(None, query.lower(), name_l).ratio()
            score += difflib.SequenceMatcher(None, query.lower(), desc_l).ratio() * 0.5
            scored.append((score, full_name, app_id, action))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "tool": full_name,
                "app_id": app_id,
                "description": action.description,
                "score": round(score, 3),
            }
            for score, full_name, app_id, action in scored[:limit]
            if score > 0
        ]

    async def execute_action(
        self,
        app_id: str,
        action_name: str,
        params: Dict[str, Any],
        auth_data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        full_name = f"{app_id}_{action_name}"
        if full_name not in self._action_handlers:
            raise ValueError(f"Action {full_name} not found.")
        handler = self._action_handlers[full_name]
        return await handler(params, auth_data)
