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

__version__ = "0.2.0"

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
