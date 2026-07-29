"""REST facade: the original HTTP API plus the dashboard, over the shared core.

Endpoint paths are unchanged from the original `server/main.py`, so existing
clients keep working. New:

- Dashboard is served from this same origin at `/` (no CORS gymnastics).
- Optional bearer auth: set OPEN_COMPOSIO_API_TOKEN to require
  `Authorization: Bearer <token>` on /api/*. Required before binding
  to anything other than 127.0.0.1.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core.executor import NotConnectedError
from .sdk import OpenComposio

DASHBOARD_DIR = Path(__file__).parent / "dashboard"


def _require_token(request: Request) -> None:
    token = os.environ.get("OPEN_COMPOSIO_API_TOKEN")
    if not token:
        return
    header = request.headers.get("authorization", "")
    if header != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token.")


def create_app(oc: Optional[OpenComposio] = None) -> FastAPI:
    oc = oc or OpenComposio()
    app = FastAPI(title="OpenComposio Server", version="0.2.0")

    # The dashboard is same-origin; this only matters for tools hitting the
    # API from other local dev servers.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/apps", dependencies=[Depends(_require_token)])
    def list_apps(user_id: str = "default_user"):
        scoped = oc if user_id == oc.user_id else _scoped(oc, user_id)
        return {"apps": scoped.get_apps()}

    @app.get("/api/apps/{app_id}/actions", dependencies=[Depends(_require_token)])
    def list_actions(app_id: str):
        try:
            return {"app_id": app_id, "actions": oc.get_actions(app_id)}
        except KeyError:
            raise HTTPException(status_code=404, detail="App not found")

    @app.post("/api/connections/{app_id}", dependencies=[Depends(_require_token)])
    def create_connection(
        app_id: str,
        user_id: str = "default_user",
        auth_data: Dict[str, Any] = Body(...),
    ):
        try:
            _scoped(oc, user_id).connect(app_id, **auth_data)
        except KeyError:
            raise HTTPException(status_code=404, detail="App not found")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"status": "success", "message": f"Connected to {app_id} successfully."}

    @app.delete("/api/connections/{app_id}", dependencies=[Depends(_require_token)])
    def remove_connection(app_id: str, user_id: str = "default_user"):
        _scoped(oc, user_id).disconnect(app_id)
        return {"status": "success", "message": f"Connection to {app_id} removed."}

    @app.post("/api/execute/{app_id}/{action_name}", dependencies=[Depends(_require_token)])
    async def execute_action(
        app_id: str,
        action_name: str,
        payload: Dict[str, Any] = Body(...),
    ):
        user_id = payload.get("user_id", "default_user")
        params = payload.get("params", {})
        if app_id not in oc.registry.apps:
            raise HTTPException(status_code=404, detail="App not found")
        try:
            result = await oc.executor.aexecute(app_id, action_name, params, user_id)
            return {"status": "success", "result": result}
        except NotConnectedError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    if DASHBOARD_DIR.exists():
        app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")

    return app


def _scoped(oc: OpenComposio, user_id: str) -> OpenComposio:
    """A lightweight view of the same core under a different user id."""
    if user_id == oc.user_id:
        return oc
    view = object.__new__(OpenComposio)
    view.__dict__.update(oc.__dict__)
    view.user_id = user_id
    return view
