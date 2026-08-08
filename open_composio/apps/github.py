"""GitHub integration.

Every action goes through :func:`_request`, so auth, transport errors, non-2xx
responses and empty (204) bodies are handled in exactly one place.

Responses are *slimmed* before they are returned. GitHub's objects are enormous
— a single repository is ~1.5 KB of JSON, an issue ~2 KB — so a 30-item listing
would sail past the executor's `max_result_bytes` and hand the agent a
truncation marker instead of data. The rule: listings carry identifying fields
only, single-object reads add the body/diff detail you asked for it by name to
get.
"""

import base64
from urllib.parse import quote

import httpx

from ..core.registry import AppDefinition, ToolRegistry

API_ROOT = "https://api.github.com"
REQUEST_TIMEOUT = 20.0
DEFAULT_PER_PAGE = 30
MAX_BODY_CHARS = 8000  # issue/PR bodies are unbounded; keep one from eating a context
MAX_FILE_CHARS = 60000

APP = AppDefinition(
    id="github",
    name="GitHub",
    description="Access your GitHub repositories, issues, pull requests, code and user profile.",
    auth_type="api_key",
    auth_config={
        "fields": [
            {
                "name": "token",
                "label": "Personal Access Token",
                "type": "password",
                "placeholder": "ghp_...",
                "required": True,
            }
        ]
    },
)


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OpenComposio",
    }


def _missing_auth(auth_data: dict = None):
    """The auth error dict, or None when a token is present.

    Exposed separately from :func:`_request` so handlers that validate params
    locally can still fail with "connect your account" first — that's the
    actionable error, and it shouldn't be masked by a parameter complaint.
    """
    if not auth_data or "token" not in auth_data:
        return {"error": "Authentication token missing. Please connect your GitHub account."}
    return None


async def _request(
    method: str,
    path: str,
    auth_data: dict = None,
    *,
    query: dict = None,
    body: dict = None,
):
    """Call the GitHub REST API.

    Returns the decoded JSON on success (``{"ok": True}`` for empty 204-style
    bodies), or ``{"error": ...}`` — never raises, so a failed call reaches the
    agent as a readable message rather than a stack trace.
    """
    unauthenticated = _missing_auth(auth_data)
    if unauthenticated:
        return unauthenticated

    params = {k: v for k, v in (query or {}).items() if v is not None}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.request(
                method,
                f"{API_ROOT}{path}",
                params=params or None,
                json=body,
                headers=_headers(auth_data["token"]),
            )
    except httpx.HTTPError as exc:
        return {"error": f"GitHub request failed: {exc}"}

    if not 200 <= response.status_code < 300:
        detail = response.text[:500]
        if response.status_code == 403 and "rate limit" in detail.lower():
            detail += " (rate limited — retry after the window resets)"
        return {"error": f"GitHub API Error ({response.status_code}): {detail}"}

    if response.status_code == 204 or not response.content:
        return {"ok": True}
    try:
        return response.json()
    except ValueError:
        return {"error": "GitHub returned a non-JSON response."}


def _failed(data) -> bool:
    return isinstance(data, dict) and "error" in data


def _paging(params: dict) -> dict:
    """Pagination query args, clamped to GitHub's 100-per-page ceiling."""
    per_page = params.get("per_page") or DEFAULT_PER_PAGE
    return {"per_page": min(int(per_page), 100), "page": params.get("page")}


def _seg(value) -> str:
    """Quote a single path segment so a slash in `owner` can't retarget the URL."""
    return quote(str(value or ""), safe="")


def _clip(text, limit: int = MAX_BODY_CHARS):
    if not isinstance(text, str) or len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text)} chars total]"


# ------------------------------------------------------------------- slimming


def _slim_user(user):
    if not isinstance(user, dict):
        return None
    return {"login": user.get("login"), "type": user.get("type"), "url": user.get("html_url")}


def _slim_repo(repo: dict) -> dict:
    return {
        "full_name": repo.get("full_name"),
        "description": repo.get("description"),
        "private": repo.get("private"),
        "fork": repo.get("fork"),
        "language": repo.get("language"),
        "stars": repo.get("stargazers_count"),
        "forks": repo.get("forks_count"),
        "open_issues": repo.get("open_issues_count"),
        "default_branch": repo.get("default_branch"),
        "updated_at": repo.get("updated_at"),
        "url": repo.get("html_url"),
    }


def _slim_issue(issue: dict, *, with_body: bool = False) -> dict:
    out = {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "user": _slim_user(issue.get("user")),
        "labels": [lbl.get("name") for lbl in issue.get("labels") or [] if isinstance(lbl, dict)],
        "assignees": [a.get("login") for a in issue.get("assignees") or [] if isinstance(a, dict)],
        "comments": issue.get("comments"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "is_pull_request": "pull_request" in issue,
        "url": issue.get("html_url"),
    }
    if with_body:
        out["body"] = _clip(issue.get("body"))
    return out


def _slim_pr(pr: dict, *, with_body: bool = False) -> dict:
    out = {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "draft": pr.get("draft"),
        "user": _slim_user(pr.get("user")),
        "head": (pr.get("head") or {}).get("label"),
        "base": (pr.get("base") or {}).get("label"),
        "merged": pr.get("merged"),
        "mergeable": pr.get("mergeable"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "url": pr.get("html_url"),
    }
    if with_body:
        out["body"] = _clip(pr.get("body"))
        for key in ("additions", "deletions", "changed_files", "commits", "merged_at"):
            if key in pr:
                out[key] = pr[key]
    return out


def _slim_commit(commit: dict) -> dict:
    detail = commit.get("commit") or {}
    return {
        "sha": commit.get("sha"),
        "message": _clip(detail.get("message"), 500),
        "author": (detail.get("author") or {}).get("name"),
        "date": (detail.get("author") or {}).get("date"),
        "url": commit.get("html_url"),
    }


def _slim_comment(comment: dict) -> dict:
    return {
        "id": comment.get("id"),
        "user": _slim_user(comment.get("user")),
        "created_at": comment.get("created_at"),
        "body": _clip(comment.get("body")),
        "url": comment.get("html_url"),
    }


def _listing(data, slim, key: str):
    """Wrap a slimmed array as ``{key: [...], "count": n}``."""
    if _failed(data):
        return data
    if not isinstance(data, list):
        return {"error": f"Unexpected GitHub response: expected a list, got {type(data).__name__}."}
    items = [slim(item) for item in data if isinstance(item, dict)]
    return {key: items, "count": len(items)}


# --------------------------------------------------------------------- user


async def github_get_user(params: dict, auth_data: dict = None):
    return await _request("GET", "/user", auth_data)


# --------------------------------------------------------------------- repos


async def github_list_repos(params: dict, auth_data: dict = None):
    owner = params.get("owner")
    path = f"/users/{_seg(owner)}/repos" if owner else "/user/repos"
    query = {**_paging(params), "sort": params.get("sort")}
    if not owner:
        query["type"] = params.get("type")
    return _listing(await _request("GET", path, auth_data, query=query), _slim_repo, "repos")


async def github_get_repo(params: dict, auth_data: dict = None):
    data = await _request(
        "GET", f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}", auth_data
    )
    return data if _failed(data) else _slim_repo(data)


async def github_create_repo(params: dict, auth_data: dict = None):
    body = {
        "name": params.get("name"),
        "description": params.get("description", ""),
        "private": params.get("private", False),
        "auto_init": params.get("auto_init", False),
    }
    data = await _request("POST", "/user/repos", auth_data, body=body)
    return data if _failed(data) else _slim_repo(data)


async def github_list_branches(params: dict, auth_data: dict = None):
    path = f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}/branches"
    data = await _request("GET", path, auth_data, query=_paging(params))
    return _listing(
        data,
        lambda b: {
            "name": b.get("name"),
            "sha": (b.get("commit") or {}).get("sha"),
            "protected": b.get("protected"),
        },
        "branches",
    )


async def github_list_commits(params: dict, auth_data: dict = None):
    path = f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}/commits"
    query = {
        **_paging(params),
        "sha": params.get("branch"),
        "path": params.get("path"),
        "author": params.get("author"),
    }
    return _listing(
        await _request("GET", path, auth_data, query=query), _slim_commit, "commits"
    )


async def github_get_file_contents(params: dict, auth_data: dict = None):
    owner, repo = _seg(params.get("owner")), _seg(params.get("repo"))
    file_path = quote(str(params.get("path") or ""), safe="/")
    data = await _request(
        "GET",
        f"/repos/{owner}/{repo}/contents/{file_path}",
        auth_data,
        query={"ref": params.get("ref")},
    )
    if _failed(data):
        return data

    if isinstance(data, list):  # the path is a directory
        return {
            "type": "directory",
            "path": params.get("path"),
            "entries": [
                {"name": e.get("name"), "path": e.get("path"), "type": e.get("type"), "size": e.get("size")}
                for e in data
                if isinstance(e, dict)
            ],
        }

    content = data.get("content") or ""
    if data.get("encoding") == "base64" and content:
        try:
            text = base64.b64decode(content).decode("utf-8", errors="replace")
        except (ValueError, TypeError) as exc:
            return {"error": f"Could not decode file contents: {exc}"}
    elif not content:
        # GitHub omits inline content for files over 1 MB.
        return {
            "type": "file",
            "path": data.get("path"),
            "size": data.get("size"),
            "error": "File is too large for inline contents; fetch it via download_url.",
            "download_url": data.get("download_url"),
        }
    else:
        text = content

    return {
        "type": "file",
        "path": data.get("path"),
        "size": data.get("size"),
        "sha": data.get("sha"),
        "content": _clip(text, MAX_FILE_CHARS),
    }


# -------------------------------------------------------------------- issues


async def github_create_issue(params: dict, auth_data: dict = None):
    path = f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}/issues"
    body = {"title": params.get("title"), "body": params.get("body", "")}
    if params.get("labels"):
        body["labels"] = params["labels"]
    if params.get("assignees"):
        body["assignees"] = params["assignees"]
    data = await _request("POST", path, auth_data, body=body)
    return data if _failed(data) else _slim_issue(data, with_body=True)


async def github_list_issues(params: dict, auth_data: dict = None):
    path = f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}/issues"
    query = {
        **_paging(params),
        "state": params.get("state", "open"),
        "labels": params.get("labels"),
        "assignee": params.get("assignee"),
        "creator": params.get("creator"),
        "sort": params.get("sort"),
    }
    data = await _request("GET", path, auth_data, query=query)
    if _failed(data):
        return data
    result = _listing(data, _slim_issue, "issues")
    if _failed(result) or params.get("include_pull_requests"):
        return result
    # GitHub's issues endpoint returns PRs too; that surprises agents asking
    # "what bugs are open", so they're dropped unless explicitly requested.
    issues = [i for i in result["issues"] if not i["is_pull_request"]]
    return {"issues": issues, "count": len(issues)}


async def github_get_issue(params: dict, auth_data: dict = None):
    path = (
        f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}"
        f"/issues/{_seg(params.get('issue_number'))}"
    )
    data = await _request("GET", path, auth_data)
    return data if _failed(data) else _slim_issue(data, with_body=True)


async def github_update_issue(params: dict, auth_data: dict = None):
    unauthenticated = _missing_auth(auth_data)
    if unauthenticated:
        return unauthenticated

    path = (
        f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}"
        f"/issues/{_seg(params.get('issue_number'))}"
    )
    # Send only what the caller supplied — a PATCH carrying `title: None` would
    # blank out fields the agent never meant to touch.
    body = {
        key: params[key]
        for key in ("title", "body", "state", "state_reason", "labels", "assignees")
        if params.get(key) is not None
    }
    if not body:
        return {"error": "Nothing to update. Provide at least one of: title, body, state, labels, assignees."}
    data = await _request("PATCH", path, auth_data, body=body)
    return data if _failed(data) else _slim_issue(data, with_body=True)


async def github_comment_on_issue(params: dict, auth_data: dict = None):
    path = (
        f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}"
        f"/issues/{_seg(params.get('issue_number'))}/comments"
    )
    data = await _request("POST", path, auth_data, body={"body": params.get("body")})
    return data if _failed(data) else _slim_comment(data)


async def github_list_issue_comments(params: dict, auth_data: dict = None):
    path = (
        f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}"
        f"/issues/{_seg(params.get('issue_number'))}/comments"
    )
    data = await _request("GET", path, auth_data, query=_paging(params))
    return _listing(data, _slim_comment, "comments")


async def github_add_labels(params: dict, auth_data: dict = None):
    path = (
        f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}"
        f"/issues/{_seg(params.get('issue_number'))}/labels"
    )
    data = await _request("POST", path, auth_data, body={"labels": params.get("labels") or []})
    if _failed(data):
        return data
    names = [lbl.get("name") for lbl in data if isinstance(lbl, dict)] if isinstance(data, list) else []
    return {"labels": names, "count": len(names)}


# ------------------------------------------------------------- pull requests


async def github_list_pull_requests(params: dict, auth_data: dict = None):
    path = f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}/pulls"
    query = {
        **_paging(params),
        "state": params.get("state", "open"),
        "base": params.get("base"),
        "head": params.get("head"),
        "sort": params.get("sort"),
    }
    return _listing(await _request("GET", path, auth_data, query=query), _slim_pr, "pull_requests")


async def github_get_pull_request(params: dict, auth_data: dict = None):
    path = (
        f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}"
        f"/pulls/{_seg(params.get('pull_number'))}"
    )
    data = await _request("GET", path, auth_data)
    return data if _failed(data) else _slim_pr(data, with_body=True)


async def github_create_pull_request(params: dict, auth_data: dict = None):
    path = f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}/pulls"
    body = {
        "title": params.get("title"),
        "head": params.get("head"),
        "base": params.get("base"),
        "body": params.get("body", ""),
        "draft": params.get("draft", False),
    }
    data = await _request("POST", path, auth_data, body=body)
    return data if _failed(data) else _slim_pr(data, with_body=True)


async def github_merge_pull_request(params: dict, auth_data: dict = None):
    path = (
        f"/repos/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}"
        f"/pulls/{_seg(params.get('pull_number'))}/merge"
    )
    body = {
        key: params[key]
        for key in ("commit_title", "commit_message", "merge_method")
        if params.get(key) is not None
    }
    data = await _request("PUT", path, auth_data, body=body or None)
    if _failed(data):
        return data
    return {
        "merged": data.get("merged"),
        "sha": data.get("sha"),
        "message": data.get("message"),
    }


# -------------------------------------------------------------------- search


async def github_search_repos(params: dict, auth_data: dict = None):
    query = {
        **_paging(params),
        "q": params.get("query"),
        "sort": params.get("sort"),
        "order": params.get("order"),
    }
    data = await _request("GET", "/search/repositories", auth_data, query=query)
    if _failed(data):
        return data
    items = [_slim_repo(r) for r in data.get("items", []) if isinstance(r, dict)]
    return {"repos": items, "count": len(items), "total_count": data.get("total_count")}


async def github_search_code(params: dict, auth_data: dict = None):
    query = {**_paging(params), "q": params.get("query")}
    data = await _request("GET", "/search/code", auth_data, query=query)
    if _failed(data):
        return data
    items = [
        {
            "name": item.get("name"),
            "path": item.get("path"),
            "repository": (item.get("repository") or {}).get("full_name"),
            "url": item.get("html_url"),
        }
        for item in data.get("items", [])
        if isinstance(item, dict)
    ]
    return {"matches": items, "count": len(items), "total_count": data.get("total_count")}


async def github_search_issues(params: dict, auth_data: dict = None):
    query = {
        **_paging(params),
        "q": params.get("query"),
        "sort": params.get("sort"),
        "order": params.get("order"),
    }
    data = await _request("GET", "/search/issues", auth_data, query=query)
    if _failed(data):
        return data
    items = [_slim_issue(i) for i in data.get("items", []) if isinstance(i, dict)]
    return {"issues": items, "count": len(items), "total_count": data.get("total_count")}


# --------------------------------------------------------------------- stars


async def github_star_repo(params: dict, auth_data: dict = None):
    path = f"/user/starred/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}"
    data = await _request("PUT", path, auth_data)
    return data if _failed(data) else {"starred": True, "repo": f"{params.get('owner')}/{params.get('repo')}"}


async def github_unstar_repo(params: dict, auth_data: dict = None):
    path = f"/user/starred/{_seg(params.get('owner'))}/{_seg(params.get('repo'))}"
    data = await _request("DELETE", path, auth_data)
    return data if _failed(data) else {"starred": False, "repo": f"{params.get('owner')}/{params.get('repo')}"}


# ------------------------------------------------------------------ registry

_REPO_PROPS = {
    "owner": {"type": "string", "description": "GitHub username or organization name"},
    "repo": {"type": "string", "description": "Repository name"},
}
_PAGING_PROPS = {
    "per_page": {
        "type": "integer",
        "description": "Results per page (1-100, default 30)",
        "minimum": 1,
        "maximum": 100,
    },
    "page": {"type": "integer", "description": "Page number, 1-based", "minimum": 1},
}
_ISSUE_NUMBER = {"type": "integer", "description": "Issue number (not the issue id)"}
_PULL_NUMBER = {"type": "integer", "description": "Pull request number"}


def register(registry: ToolRegistry) -> None:
    registry.register_app(APP.model_copy(deep=True))

    def action(name, handler, description, properties=None, required=None, **extra):
        schema = {
            "description": description,
            "type": "object",
            "properties": properties or {},
            **extra,
        }
        if required:
            schema["required"] = required
        registry.register_action(
            app_id="github", action_name=name, schema=schema, handler=handler
        )

    # ------------------------------------------------------------------ user
    action(
        "get_user",
        github_get_user,
        "Get authenticated user profile details from GitHub.",
        **{
            "x-destructive": False,
            "x-verify": True,  # cheap read-only probe for `connect --verify`
            "x-cache-ttl": 60,
        },
    )

    # ----------------------------------------------------------------- repos
    action(
        "list_repos",
        github_list_repos,
        "List GitHub repositories for the authenticated user, or for a given user or organization.",
        {
            "owner": {
                "type": "string",
                "description": "User or org whose repos to list. Omit for your own repositories.",
            },
            "type": {
                "type": "string",
                "enum": ["all", "owner", "member"],
                "description": "Only when listing your own repos",
            },
            "sort": {"type": "string", "enum": ["created", "updated", "pushed", "full_name"]},
            **_PAGING_PROPS,
        },
        **{"x-destructive": False, "x-cache-ttl": 60},
    )
    action(
        "get_repo",
        github_get_repo,
        "Get details about a single GitHub repository: description, language, stars, default branch.",
        dict(_REPO_PROPS),
        ["owner", "repo"],
        **{"x-destructive": False, "x-cache-ttl": 60},
    )
    action(
        "create_repo",
        github_create_repo,
        "Create a new GitHub repository owned by the authenticated user.",
        {
            "name": {"type": "string", "description": "Name for the new repository"},
            "description": {"type": "string"},
            "private": {"type": "boolean", "description": "Create it as private (default false)"},
            "auto_init": {"type": "boolean", "description": "Initialize with a README"},
        },
        ["name"],
        **{"x-destructive": True},
    )
    action(
        "list_branches",
        github_list_branches,
        "List the branches of a GitHub repository.",
        {**_REPO_PROPS, **_PAGING_PROPS},
        ["owner", "repo"],
        **{"x-destructive": False, "x-cache-ttl": 60},
    )
    action(
        "list_commits",
        github_list_commits,
        "List recent commits on a GitHub repository, optionally filtered by branch, file path or author.",
        {
            **_REPO_PROPS,
            "branch": {"type": "string", "description": "Branch or SHA to list from"},
            "path": {"type": "string", "description": "Only commits touching this file path"},
            "author": {"type": "string", "description": "GitHub login or email of the author"},
            **_PAGING_PROPS,
        },
        ["owner", "repo"],
        **{"x-destructive": False, "x-cache-ttl": 60},
    )
    action(
        "get_file_contents",
        github_get_file_contents,
        "Read the contents of a file in a GitHub repository, or list a directory's entries.",
        {
            **_REPO_PROPS,
            "path": {"type": "string", "description": "Path within the repo, e.g. src/main.py"},
            "ref": {"type": "string", "description": "Branch, tag or commit SHA (default branch if omitted)"},
        },
        ["owner", "repo", "path"],
        **{"x-destructive": False, "x-cache-ttl": 60},
    )

    # ---------------------------------------------------------------- issues
    action(
        "create_issue",
        github_create_issue,
        "Create a new issue in a GitHub repository — file a bug report, feature request or task.",
        {
            **_REPO_PROPS,
            "title": {"type": "string", "description": "Title of the issue"},
            "body": {"type": "string", "description": "Body description of the issue"},
            "labels": {"type": "array", "items": {"type": "string"}},
            "assignees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "GitHub logins to assign",
            },
        },
        ["owner", "repo", "title"],
        **{"x-destructive": True},
    )
    action(
        "list_issues",
        github_list_issues,
        "List issues in a GitHub repository, filtered by state, label or assignee.",
        {
            **_REPO_PROPS,
            "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Default open"},
            "labels": {"type": "string", "description": "Comma-separated label names"},
            "assignee": {"type": "string", "description": "GitHub login, or '*' for any, 'none' for unassigned"},
            "creator": {"type": "string", "description": "GitHub login of the issue author"},
            "sort": {"type": "string", "enum": ["created", "updated", "comments"]},
            "include_pull_requests": {
                "type": "boolean",
                "description": "GitHub counts PRs as issues; they are excluded unless this is true",
            },
            **_PAGING_PROPS,
        },
        ["owner", "repo"],
        **{"x-destructive": False, "x-cache-ttl": 30},
    )
    action(
        "get_issue",
        github_get_issue,
        "Get a single GitHub issue by number, including its full body text.",
        {**_REPO_PROPS, "issue_number": _ISSUE_NUMBER},
        ["owner", "repo", "issue_number"],
        **{"x-destructive": False, "x-cache-ttl": 30},
    )
    action(
        "update_issue",
        github_update_issue,
        "Update a GitHub issue — edit its title or body, or close and reopen it.",
        {
            **_REPO_PROPS,
            "issue_number": _ISSUE_NUMBER,
            "title": {"type": "string"},
            "body": {"type": "string"},
            "state": {"type": "string", "enum": ["open", "closed"], "description": "Close or reopen the issue"},
            "state_reason": {"type": "string", "enum": ["completed", "not_planned", "reopened"]},
            "labels": {"type": "array", "items": {"type": "string"}, "description": "Replaces existing labels"},
            "assignees": {"type": "array", "items": {"type": "string"}},
        },
        ["owner", "repo", "issue_number"],
        **{"x-destructive": True},
    )
    action(
        "comment_on_issue",
        github_comment_on_issue,
        "Post a comment on a GitHub issue or pull request.",
        {
            **_REPO_PROPS,
            "issue_number": _ISSUE_NUMBER,
            "body": {"type": "string", "description": "Markdown text of the comment"},
        },
        ["owner", "repo", "issue_number", "body"],
        **{"x-destructive": True},
    )
    action(
        "list_issue_comments",
        github_list_issue_comments,
        "List the comments on a GitHub issue or pull request.",
        {**_REPO_PROPS, "issue_number": _ISSUE_NUMBER, **_PAGING_PROPS},
        ["owner", "repo", "issue_number"],
        **{"x-destructive": False, "x-cache-ttl": 30},
    )
    action(
        "add_labels",
        github_add_labels,
        "Add labels to a GitHub issue or pull request, keeping the existing ones.",
        {
            **_REPO_PROPS,
            "issue_number": _ISSUE_NUMBER,
            "labels": {"type": "array", "items": {"type": "string"}, "description": "Label names to add"},
        },
        ["owner", "repo", "issue_number", "labels"],
        **{"x-destructive": True},
    )

    # --------------------------------------------------------- pull requests
    action(
        "list_pull_requests",
        github_list_pull_requests,
        "List pull requests in a GitHub repository, filtered by state or target branch.",
        {
            **_REPO_PROPS,
            "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Default open"},
            "base": {"type": "string", "description": "Branch the PR merges into"},
            "head": {"type": "string", "description": "Source branch, as user:branch"},
            "sort": {"type": "string", "enum": ["created", "updated", "popularity"]},
            **_PAGING_PROPS,
        },
        ["owner", "repo"],
        **{"x-destructive": False, "x-cache-ttl": 30},
    )
    action(
        "get_pull_request",
        github_get_pull_request,
        "Get a single GitHub pull request by number, with its body, merge state and diff size.",
        {**_REPO_PROPS, "pull_number": _PULL_NUMBER},
        ["owner", "repo", "pull_number"],
        **{"x-destructive": False, "x-cache-ttl": 30},
    )
    action(
        "create_pull_request",
        github_create_pull_request,
        "Open a new pull request in a GitHub repository.",
        {
            **_REPO_PROPS,
            "title": {"type": "string"},
            "head": {"type": "string", "description": "Branch containing the changes (user:branch if cross-fork)"},
            "base": {"type": "string", "description": "Branch to merge into, e.g. main"},
            "body": {"type": "string", "description": "Markdown description of the PR"},
            "draft": {"type": "boolean", "description": "Open it as a draft"},
        },
        ["owner", "repo", "title", "head", "base"],
        **{"x-destructive": True},
    )
    action(
        "merge_pull_request",
        github_merge_pull_request,
        "Merge an open GitHub pull request into its base branch.",
        {
            **_REPO_PROPS,
            "pull_number": _PULL_NUMBER,
            "commit_title": {"type": "string"},
            "commit_message": {"type": "string"},
            "merge_method": {"type": "string", "enum": ["merge", "squash", "rebase"]},
        },
        ["owner", "repo", "pull_number"],
        **{"x-destructive": True},
    )

    # ---------------------------------------------------------------- search
    action(
        "search_repos",
        github_search_repos,
        "Search GitHub for repositories matching a query, e.g. 'mcp gateway language:python stars:>100'.",
        {
            "query": {"type": "string", "description": "GitHub search query with optional qualifiers"},
            "sort": {"type": "string", "enum": ["stars", "forks", "updated"]},
            "order": {"type": "string", "enum": ["asc", "desc"]},
            **_PAGING_PROPS,
        },
        ["query"],
        **{"x-destructive": False, "x-cache-ttl": 120},
    )
    action(
        "search_code",
        github_search_code,
        "Search GitHub source code for a string, e.g. 'AsyncClient repo:encode/httpx'.",
        {
            "query": {
                "type": "string",
                "description": "Code search query; qualifiers like repo:, org:, language:, path: are supported",
            },
            **_PAGING_PROPS,
        },
        ["query"],
        **{"x-destructive": False, "x-cache-ttl": 120},
    )
    action(
        "search_issues",
        github_search_issues,
        "Search GitHub issues and pull requests across repositories, e.g. 'is:open assignee:@me'.",
        {
            "query": {"type": "string", "description": "GitHub issue search query with qualifiers"},
            "sort": {"type": "string", "enum": ["created", "updated", "comments"]},
            "order": {"type": "string", "enum": ["asc", "desc"]},
            **_PAGING_PROPS,
        },
        ["query"],
        **{"x-destructive": False, "x-cache-ttl": 120},
    )

    # ----------------------------------------------------------------- stars
    action(
        "star_repo",
        github_star_repo,
        "Star a GitHub repository as the authenticated user.",
        dict(_REPO_PROPS),
        ["owner", "repo"],
        **{"x-destructive": True},
    )
    action(
        "unstar_repo",
        github_unstar_repo,
        "Remove your star from a GitHub repository.",
        dict(_REPO_PROPS),
        ["owner", "repo"],
        **{"x-destructive": True},
    )
