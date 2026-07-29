from ..core.registry import ToolRegistry
from . import github, weather, web_search

BUILTIN_APPS = [github, weather, web_search]


def load_builtin_apps(registry: ToolRegistry) -> None:
    for module in BUILTIN_APPS:
        module.register(registry)
