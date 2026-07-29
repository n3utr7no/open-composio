import os
import json
from typing import Dict, Any, Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CONNECTIONS_FILE = os.path.join(DATA_DIR, "connections.json")

def init_db():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    if not os.path.exists(CONNECTIONS_FILE):
        with open(CONNECTIONS_FILE, "w") as f:
            json.dump({}, f)

def load_all_connections() -> Dict[str, Any]:
    init_db()
    try:
        with open(CONNECTIONS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_all_connections(data: Dict[str, Any]):
    init_db()
    with open(CONNECTIONS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_connection(user_id: str, app_id: str) -> Optional[Dict[str, Any]]:
    db = load_all_connections()
    return db.get(user_id, {}).get(app_id)

def save_connection(user_id: str, app_id: str, auth_data: Dict[str, Any]):
    db = load_all_connections()
    if user_id not in db:
        db[user_id] = {}
    db[user_id][app_id] = auth_data
    save_all_connections(db)

def delete_connection(user_id: str, app_id: str):
    db = load_all_connections()
    if user_id in db and app_id in db[user_id]:
        del db[user_id][app_id]
        save_all_connections(db)

def list_connections(user_id: str) -> Dict[str, Any]:
    db = load_all_connections()
    return db.get(user_id, {})
