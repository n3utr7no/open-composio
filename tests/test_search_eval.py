"""Retrieval eval for `search_tools` — the quality bar the MCP gateway rests on.

Every builtin action must be findable from a natural task phrasing. When an
integration is added, add at least one (query, expected_tool) pair here; CI
fails if retrieval regresses.
"""

import pytest

from open_composio import OpenComposio
from open_composio.core.vault import EncryptedFileVault

# (query, expected tool, must be top-1?)
EVAL_SET = [
    ("create an issue on github", "github_create_issue", True),
    ("file a bug report in a github repository", "github_create_issue", False),
    ("get my github user profile", "github_get_user", True),
    ("who is the authenticated github user", "github_get_user", False),
    ("current weather for a city", "weather_get_current", True),
    ("temperature and humidity in tokyo", "weather_get_current", False),
    ("search the web", "web_search_search", True),
    ("duckduckgo search for a phrase", "web_search_search", False),
]


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    oc = OpenComposio(
        vault=EncryptedFileVault(directory=tmp_path_factory.mktemp("vault")), audit=False
    )
    return oc.registry


@pytest.mark.parametrize("query,expected,top1", EVAL_SET, ids=[q for q, _, _ in EVAL_SET])
def test_search_recall(registry, query, expected, top1):
    results = [r["tool"] for r in registry.search(query, limit=3)]
    assert expected in results, f"{expected!r} not in top-3 for {query!r}: {results}"
    if top1:
        assert results[0] == expected, f"expected {expected!r} top-1 for {query!r}, got {results}"
