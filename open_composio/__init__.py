"""open-composio: the local-first MCP tool gateway.

One MCP server, an embeddable SDK, and a credential vault that never
leaves your machine.
"""

from .sdk import OpenComposio, Tool, ToolCollection
from .core import (
    AppDefinition,
    ActionDefinition,
    ToolRegistry,
    Executor,
    ExecutionContext,
    NotConnectedError,
    PermissionDenied,
)

try:
    from importlib.metadata import version as _version

    __version__ = _version("open-composio")
except Exception:  # not installed (e.g. vendored copy)
    __version__ = "0.0.0+unknown"

__all__ = [
    "OpenComposio",
    "Tool",
    "ToolCollection",
    "AppDefinition",
    "ActionDefinition",
    "ToolRegistry",
    "Executor",
    "ExecutionContext",
    "NotConnectedError",
    "PermissionDenied",
    "__version__",
]
