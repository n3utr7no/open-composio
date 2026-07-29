import httpx
import re
import html as html_parser
from urllib.parse import quote
from registry import registry, AppDefinition

app = AppDefinition(
    id="web_search",
    name="Web Search",
    description="Search the web for information using DuckDuckGo",
    auth_type="none"
)
registry.register_app(app)

async def search_duckduckgo(params: dict, auth_data: dict = None):
    query = params.get("query")
    if not query:
        return {"error": "Query parameter is required"}
    
    # We will use DuckDuckGo's simple HTML search or a free search API
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code != 200:
                return {"error": f"Failed to fetch results: HTTP {response.status_code}"}
            
            # Simple text parsing for DuckDuckGo HTML results
            # Results are usually inside links with class "result__snippet" and titles in "result__a"

            
            html = response.text
            # Simple regex search for titles, links, snippets
            results = []
            
            # Let's extract blocks of results
            # DuckDuckGo HTML structure has <div class="result results_links results_links_deep web-result ">
            blocks = html.split('<div class="result results_links')
            for block in blocks[1:6]: # Limit to top 5 results
                # Find title and link
                a_match = re.search(r'<a class="result__url" href="([^"]+)"', block)
                title_match = re.search(r'<a class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
                snippet_match = re.search(r'<a class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
                # Or parsing result__a and result__snippet
                title_match = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
                snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
                url_match = re.search(r'href="([^"]+)"', block)
                
                if title_match and url_match:
                    title = re.sub('<[^<]+?>', '', title_match.group(1)).strip()
                    url = url_match.group(1)
                    snippet = ""
                    if snippet_match:
                        snippet = re.sub('<[^<]+?>', '', snippet_match.group(1)).strip()
                    

                    results.append({
                        "title": html_parser.unescape(title),
                        "url": html_parser.unescape(url),
                        "snippet": html_parser.unescape(snippet)
                    })
            
            if not results:
                # Fallback to a mock/simple response if html structure changed
                return {"results": [{"title": f"Search for {query}", "url": f"https://duckduckgo.com/?q={query}", "snippet": "No organic results found. Click link to view DuckDuckGo search."}]}
                
            return {"results": results}
    except Exception as e:
        return {"error": str(e)}

registry.register_action(
    app_id="web_search",
    action_name="search",
    schema={
        "description": "Perform a search query on DuckDuckGo and return top results.",
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term or phrase to look for"
            }
        },
        "required": ["query"]
    },
    handler=search_duckduckgo
)
