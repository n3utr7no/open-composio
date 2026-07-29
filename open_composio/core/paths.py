import os
from pathlib import Path


def data_dir() -> Path:
    """Local data directory for vault, audit log and connection index.

    Defaults to ``~/.open-composio``; override with OPEN_COMPOSIO_HOME.
    """
    root = os.environ.get("OPEN_COMPOSIO_HOME")
    path = Path(root) if root else Path.home() / ".open-composio"
    path.mkdir(parents=True, exist_ok=True)
    return path
