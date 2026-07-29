from typing import Dict, Any, Callable, List, Optional
from pydantic import BaseModel, Field

class ActionDefinition(BaseModel):
    name: str
    description: str
    parameters_schema: Dict[str, Any]  # JSON Schema of input parameters

class AppDefinition(BaseModel):
    id: str
    name: str
    description: str
    auth_type: str  # "none", "api_key", "oauth2"
    auth_config: Dict[str, Any] = Field(default_factory=dict)
    actions: Dict[str, ActionDefinition] = Field(default_factory=dict)

class ToolRegistry:
    def __init__(self):
        self.apps: Dict[str, AppDefinition] = {}
        self._action_handlers: Dict[str, Callable] = {}

    def register_app(self, app: AppDefinition):
        self.apps[app.id] = app

    def register_action(self, app_id: str, action_name: str, schema: Dict[str, Any], handler: Callable):
        if app_id not in self.apps:
            raise ValueError(f"App {app_id} is not registered.")
        
        full_action_name = f"{app_id}_{action_name}"
        self.apps[app_id].actions[action_name] = ActionDefinition(
            name=action_name,
            description=schema.get("description", ""),
            parameters_schema=schema
        )
        self._action_handlers[full_action_name] = handler

    async def execute_action(self, app_id: str, action_name: str, params: Dict[str, Any], auth_data: Optional[Dict[str, Any]] = None) -> Any:
        full_action_name = f"{app_id}_{action_name}"
        if full_action_name not in self._action_handlers:
            raise ValueError(f"Action {full_action_name} not found.")
        
        handler = self._action_handlers[full_action_name]
        return await handler(params, auth_data)

registry = ToolRegistry()
