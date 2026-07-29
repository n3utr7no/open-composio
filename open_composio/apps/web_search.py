import html as html_parser
import re
from urllib.parse import quote

import httpx

from ..core.registry import AppDefinition, ToolRegistry

APP = AppDefinition(
    id="web_search",
    name="Web Search",
    description="Search the web for information using DuckDuckGo",
    auth_type="none",
)


async def search_duckduckgo(params: dict, auth_data: dict = None):
    query = params.get("query")
    if not query:
        return {"error": "Query parameter is required"}

    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code != 200:
                return {"error": f"Failed to fetch results: HTTP {response.status_code}"}

            html = response.text
            results = []
            # DuckDuckGo HTML results come as <div class="result results_links ..."> blocks
            blocks = html.split('<div class="result results_links')
            for block in blocks[1:6]:  # top 5 results
                title_match = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
                snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
                url_match = re.search(r'href="([^"]+)"', block)

                if title_match and url_match:
                    title = re.sub("<[^<]+?>", "", title_match.group(1)).strip()
                    snippet = ""
                    if snippet_match:
                        snippet = re.sub("<[^<]+?>", "", snippet_match.group(1)).strip()
                    results.append(
                        {
                            "title": html_parser.unescape(title),
                            "url": html_parser.unescape(url_match.group(1)),
                            "snippet": html_parser.unescape(snippet),
                        }
                    )

            if not results:
                # Fallback if DuckDuckGo's html structure changed
                return {
                    "results": [
                        {
                            "title": f"Search for {query}",
                            "url": f"https://duckduckgo.com/?q={quote(query)}",
                            "snippet": "No organic results found. Click link to view DuckDuckGo search.",
                        }
                    ]
                }
            return {"results": results}
    except Exception as e:
        return {"error": str(e)}


def register(registry: ToolRegistry) -> None:
    registry.register_app(APP.model_copy(deep=True))
    registry.register_action(
        app_id="web_search",
        action_name="search",
        schema={
            "description": "Perform a search query on DuckDuckGo and return top results.",
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search term or phrase to look for",
                }
            },
            "required": ["query"],
        },
        handler=search_duckduckgo,
    )
