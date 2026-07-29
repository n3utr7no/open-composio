import httpx
from registry import registry, AppDefinition

app = AppDefinition(
    id="github",
    name="GitHub",
    description="Access your GitHub repositories, issues, and user profiles.",
    auth_type="api_key",
    auth_config={
        "fields": [
            {
                "name": "token",
                "label": "Personal Access Token",
                "type": "password",
                "placeholder": "ghp_...",
                "required": True
            }
        ]
    }
)
registry.register_app(app)

async def github_get_user(params: dict, auth_data: dict = None):
    if not auth_data or "token" not in auth_data:
        return {"error": "Authentication token missing. Please connect your GitHub account."}
    
    token = auth_data["token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OpenComposio"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get("https://api.github.com/user", headers=headers)
        if response.status_code != 200:
            return {"error": f"GitHub API Error: {response.text}"}
        return response.json()

async def github_create_issue(params: dict, auth_data: dict = None):
    if not auth_data or "token" not in auth_data:
        return {"error": "Authentication token missing. Please connect your GitHub account."}
    
    owner = params.get("owner")
    repo = params.get("repo")
    title = params.get("title")
    body = params.get("body", "")
    
    token = auth_data["token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OpenComposio"
    }
    
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json={"title": title, "body": body}, headers=headers)
        if response.status_code != 201:
            return {"error": f"Failed to create issue: {response.text}"}
        return response.json()

registry.register_action(
    app_id="github",
    action_name="get_user",
    schema={
        "description": "Get authenticated user profile details from GitHub.",
        "type": "object",
        "properties": {}
    },
    handler=github_get_user
)

registry.register_action(
    app_id="github",
    action_name="create_issue",
    schema={
        "description": "Create a new issue in a GitHub repository.",
        "type": "object",
        "properties": {
            "owner": {
                "type": "string",
                "description": "GitHub username or organization name"
            },
            "repo": {
                "type": "string",
                "description": "Repository name"
            },
            "title": {
                "type": "string",
                "description": "Title of the issue"
            },
            "body": {
                "type": "string",
                "description": "Body description of the issue"
            }
        },
        "required": ["owner", "repo", "title"]
    },
    handler=github_create_issue
)
