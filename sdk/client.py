import httpx
from typing import Dict, Any, List, Optional, Callable

class OpenComposio:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", user_id: str = "default_user"):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.client = httpx.Client()

    def get_apps(self) -> List[Dict[str, Any]]:
        """List all available integrations and connection statuses."""
        response = self.client.get(f"{self.base_url}/api/apps?user_id={self.user_id}")
        response.raise_for_status()
        return response.json().get("apps", [])

    def get_actions(self, app_id: str) -> List[Dict[str, Any]]:
        """Get all actions for a specific integration."""
        response = self.client.get(f"{self.base_url}/api/apps/{app_id}/actions")
        response.raise_for_status()
        return response.json().get("actions", [])

    def execute(self, app_id: str, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool action."""
        payload = {
            "user_id": self.user_id,
            "params": params
        }
        response = self.client.post(
            f"{self.base_url}/api/execute/{app_id}/{action_name}",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    def load_as_tools(self, app_id: str) -> List[Dict[str, Any]]:
        """Load actions formatted as OpenAI-style function definitions."""
        actions = self.get_actions(app_id)
        tools = []
        for action in actions:
            tools.append({
                "type": "function",
                "function": {
                    "name": f"{app_id}_{action['name']}",
                    "description": action["description"],
                    "parameters": action["parameters_schema"]
                }
            })
        return tools
