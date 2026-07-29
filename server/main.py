import uvicorn
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, Optional

from registry import registry
import connections

# Import apps to trigger registration
import apps.web_search
import apps.weather
import apps.github

app = FastAPI(title="OpenComposio Server", version="0.1.0")

# Enable CORS for frontend dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/apps")
def list_apps(user_id: str = "default_user"):
    """List all registered apps and their connection status."""
    connected_apps = connections.list_connections(user_id)
    result = []
    for app_id, app_def in registry.apps.items():
        result.append({
            "id": app_def.id,
            "name": app_def.name,
            "description": app_def.description,
            "auth_type": app_def.auth_type,
            "auth_config": app_def.auth_config,
            "connected": app_id in connected_apps
        })
    return {"apps": result}

@app.get("/api/apps/{app_id}/actions")
def list_actions(app_id: str):
    """List all actions for a given app."""
    if app_id not in registry.apps:
        raise HTTPException(status_code=404, detail="App not found")
    
    app_def = registry.apps[app_id]
    return {
        "app_id": app_id,
        "actions": [
            {
                "name": action_name,
                "description": action.description,
                "parameters_schema": action.parameters_schema
            }
            for action_name, action in app_def.actions.items()
        ]
    }

@app.post("/api/connections/{app_id}")
def create_connection(
    app_id: str,
    user_id: str = "default_user",
    auth_data: Dict[str, Any] = Body(...)
):
    """Store or update auth credentials for an app."""
    if app_id not in registry.apps:
        raise HTTPException(status_code=404, detail="App not found")
    
    connections.save_connection(user_id, app_id, auth_data)
    return {"status": "success", "message": f"Connected to {app_id} successfully."}

@app.delete("/api/connections/{app_id}")
def remove_connection(app_id: str, user_id: str = "default_user"):
    """Remove connection credentials."""
    connections.delete_connection(user_id, app_id)
    return {"status": "success", "message": f"Connection to {app_id} removed."}

@app.post("/api/execute/{app_id}/{action_name}")
async def execute_action(
    app_id: str,
    action_name: str,
    payload: Dict[str, Any] = Body(...)
):
    """Execute a specific action on an app."""
    user_id = payload.get("user_id", "default_user")
    params = payload.get("params", {})
    
    if app_id not in registry.apps:
        raise HTTPException(status_code=404, detail="App not found")
    
    app_def = registry.apps[app_id]
    
    # Retrieve auth info if needed
    auth_data = None
    if app_def.auth_type != "none":
        auth_data = connections.get_connection(user_id, app_id)
        if not auth_data:
            raise HTTPException(
                status_code=401,
                detail=f"App '{app_id}' requires authentication. Please connect it first."
            )
            
    try:
        result = await registry.execute_action(app_id, action_name, params, auth_data)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
