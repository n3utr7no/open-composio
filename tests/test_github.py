"""GitHub integration: routing, slimming, and failure handling.

No test here touches the network — `gh` swaps the module's httpx client for an
`httpx.MockTransport` that serves canned responses and records every request, so
we can assert on the URL, query string and JSON body the handler actually sent.
"""

import base64
import json

import httpx
import pytest

from open_composio.apps import github
from open_composio.core.policy import is_destructive

AUTH = {"token": "ghp_test"}


class FakeGitHub:
    def __init__(self):
        self.routes = {}  # (method, path) -> (status, payload)
        self.requests = []

    def route(self, method, path, payload, status=200):
        self.routes[(method, path)] = (status, payload)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        entry = self.routes.get((request.method, request.url.path))
        if entry is None:
            return httpx.Response(
                404, json={"message": f"no stub for {request.method} {request.url.path}"}
            )
        status, payload = entry
        if payload is None:
            return httpx.Response(status)
        return httpx.Response(status, json=payload)

    # ------------------------------------------------------------- assertions
    @property
    def last(self) -> httpx.Request:
        assert self.requests, "no request was made"
        return self.requests[-1]

    @property
    def last_body(self) -> dict:
        return json.loads(self.last.content)

    def query(self, key):
        return self.last.url.params.get(key)


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake.handle)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(github.httpx, "AsyncClient", client_factory)
    return fake


# ------------------------------------------------------------------ fixtures

REPO = {
    "full_name": "octo/hello",
    "description": "greetings",
    "private": False,
    "fork": False,
    "language": "Python",
    "stargazers_count": 12,
    "forks_count": 3,
    "open_issues_count": 1,
    "default_branch": "main",
    "updated_at": "2026-01-01T00:00:00Z",
    "html_url": "https://github.com/octo/hello",
    "owner": {"login": "octo", "type": "User", "html_url": "https://github.com/octo"},
    "id": 1,
    "node_id": "junk",
    "permissions": {"admin": True},
}

ISSUE = {
    "number": 7,
    "title": "Broken import",
    "state": "open",
    "body": "Traceback goes here",
    "user": {"login": "octo", "type": "User", "html_url": "https://github.com/octo"},
    "labels": [{"name": "bug"}, {"name": "p1"}],
    "assignees": [{"login": "dev1"}],
    "comments": 2,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "html_url": "https://github.com/octo/hello/issues/7",
}

PULL = {
    "number": 42,
    "title": "Add caching",
    "state": "open",
    "draft": False,
    "body": "Speeds things up",
    "user": {"login": "octo", "type": "User", "html_url": "https://github.com/octo"},
    "head": {"label": "octo:feature"},
    "base": {"label": "octo:main"},
    "merged": False,
    "mergeable": True,
    "additions": 10,
    "deletions": 2,
    "changed_files": 1,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "html_url": "https://github.com/octo/hello/pull/42",
}


# ------------------------------------------------------- transport / failures


async def test_missing_auth_is_reported_by_every_action(gh):
    """No handler may reach the network unauthenticated."""
    handlers = [
        value
        for name, value in vars(github).items()
        if name.startswith("github_") and callable(value)
    ]
    assert len(handlers) >= 20  # guard against the introspection silently matching nothing

    for handler in handlers:
        result = await handler({"owner": "o", "repo": "r"}, None)
        assert "error" in result, handler.__name__
        assert "token" in result["error"].lower(), handler.__name__
    assert gh.requests == []


async def test_http_error_includes_status_and_detail(gh):
    gh.route("GET", "/repos/octo/hello", {"message": "Not Found"}, status=404)
    result = await github.github_get_repo({"owner": "octo", "repo": "hello"}, AUTH)
    assert "404" in result["error"]
    assert "Not Found" in result["error"]


async def test_rate_limit_error_is_annotated(gh):
    gh.route("GET", "/user", {"message": "API rate limit exceeded"}, status=403)
    result = await github.github_get_user({}, AUTH)
    assert "rate limited" in result["error"]


async def test_transport_failure_returns_error_not_exception(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("dns failure")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        github.httpx,
        "AsyncClient",
        lambda *a, **kw: real_client(*a, **{**kw, "transport": httpx.MockTransport(boom)}),
    )
    result = await github.github_get_user({}, AUTH)
    assert "GitHub request failed" in result["error"]


async def test_auth_headers_sent(gh):
    gh.route("GET", "/user", {"login": "octo"})
    await github.github_get_user({}, AUTH)
    assert gh.last.headers["authorization"] == "Bearer ghp_test"
    assert gh.last.headers["x-github-api-version"] == "2022-11-28"


async def test_empty_204_body_becomes_ok(gh):
    gh.route("PUT", "/user/starred/octo/hello", None, status=204)
    assert await github.github_star_repo({"owner": "octo", "repo": "hello"}, AUTH) == {
        "starred": True,
        "repo": "octo/hello",
    }


async def test_path_segments_are_quoted(gh):
    """A slash in `owner` must not be able to retarget the URL.

    Asserted on `raw_path` — that's what goes on the wire; `.path` is the
    percent-decoded view and would hide the escaping.
    """
    await github.github_get_repo({"owner": "../../user", "repo": "x"}, AUTH)
    assert gh.last.url.raw_path == b"/repos/..%2F..%2Fuser/x"


# --------------------------------------------------------------------- repos


async def test_list_repos_defaults_to_authenticated_user(gh):
    gh.route("GET", "/user/repos", [REPO])
    result = await github.github_list_repos({}, AUTH)
    assert result["count"] == 1
    assert result["repos"][0]["full_name"] == "octo/hello"
    assert result["repos"][0]["stars"] == 12
    assert "node_id" not in result["repos"][0]  # slimmed
    assert gh.query("per_page") == "30"


async def test_list_repos_for_named_owner(gh):
    gh.route("GET", "/users/anthropics/repos", [REPO])
    result = await github.github_list_repos({"owner": "anthropics"}, AUTH)
    assert result["count"] == 1
    assert gh.query("type") is None  # `type` is only valid on /user/repos


async def test_per_page_clamped_to_github_ceiling(gh):
    gh.route("GET", "/user/repos", [])
    await github.github_list_repos({"per_page": 5000}, AUTH)
    assert gh.query("per_page") == "100"


async def test_get_repo_slims_response(gh):
    gh.route("GET", "/repos/octo/hello", REPO)
    result = await github.github_get_repo({"owner": "octo", "repo": "hello"}, AUTH)
    assert result["default_branch"] == "main"
    assert set(result) == {
        "full_name", "description", "private", "fork", "language", "stars",
        "forks", "open_issues", "default_branch", "updated_at", "url",
    }


async def test_create_repo_posts_body(gh):
    gh.route("POST", "/user/repos", REPO, status=201)
    result = await github.github_create_repo({"name": "hello", "private": True}, AUTH)
    assert gh.last_body == {
        "name": "hello", "description": "", "private": True, "auto_init": False
    }
    assert result["full_name"] == "octo/hello"


async def test_list_branches(gh):
    gh.route(
        "GET",
        "/repos/octo/hello/branches",
        [{"name": "main", "commit": {"sha": "abc123"}, "protected": True}],
    )
    result = await github.github_list_branches({"owner": "octo", "repo": "hello"}, AUTH)
    assert result["branches"] == [{"name": "main", "sha": "abc123", "protected": True}]


async def test_list_commits_passes_filters(gh):
    gh.route(
        "GET",
        "/repos/octo/hello/commits",
        [
            {
                "sha": "abc",
                "html_url": "https://github.com/octo/hello/commit/abc",
                "commit": {
                    "message": "Fix the thing",
                    "author": {"name": "Octo", "date": "2026-01-01T00:00:00Z"},
                },
            }
        ],
    )
    result = await github.github_list_commits(
        {"owner": "octo", "repo": "hello", "branch": "dev", "path": "src/app.py"}, AUTH
    )
    assert gh.query("sha") == "dev"  # GitHub calls the branch param `sha`
    assert gh.query("path") == "src/app.py"
    assert result["commits"][0] == {
        "sha": "abc",
        "message": "Fix the thing",
        "author": "Octo",
        "date": "2026-01-01T00:00:00Z",
        "url": "https://github.com/octo/hello/commit/abc",
    }


# ----------------------------------------------------------------- file read


async def test_get_file_contents_decodes_base64(gh):
    gh.route(
        "GET",
        "/repos/octo/hello/contents/src/app.py",
        {
            "path": "src/app.py",
            "size": 11,
            "sha": "deadbeef",
            "encoding": "base64",
            "content": base64.b64encode(b"print('hi')").decode(),
        },
    )
    result = await github.github_get_file_contents(
        {"owner": "octo", "repo": "hello", "path": "src/app.py", "ref": "dev"}, AUTH
    )
    assert result["type"] == "file"
    assert result["content"] == "print('hi')"
    assert gh.query("ref") == "dev"


async def test_get_file_contents_lists_a_directory(gh):
    gh.route(
        "GET",
        "/repos/octo/hello/contents/src",
        [{"name": "app.py", "path": "src/app.py", "type": "file", "size": 11}],
    )
    result = await github.github_get_file_contents(
        {"owner": "octo", "repo": "hello", "path": "src"}, AUTH
    )
    assert result["type"] == "directory"
    assert result["entries"][0]["name"] == "app.py"


async def test_oversized_file_reports_download_url(gh):
    gh.route(
        "GET",
        "/repos/octo/hello/contents/big.bin",
        {
            "path": "big.bin",
            "size": 2_000_000,
            "encoding": "none",
            "content": "",
            "download_url": "https://raw.githubusercontent.com/octo/hello/main/big.bin",
        },
    )
    result = await github.github_get_file_contents(
        {"owner": "octo", "repo": "hello", "path": "big.bin"}, AUTH
    )
    assert "too large" in result["error"]
    assert result["download_url"].endswith("big.bin")


async def test_file_contents_are_clipped(gh):
    huge = "x" * (github.MAX_FILE_CHARS + 5000)
    gh.route(
        "GET",
        "/repos/octo/hello/contents/big.txt",
        {
            "path": "big.txt",
            "encoding": "base64",
            "content": base64.b64encode(huge.encode()).decode(),
        },
    )
    result = await github.github_get_file_contents(
        {"owner": "octo", "repo": "hello", "path": "big.txt"}, AUTH
    )
    assert len(result["content"]) < len(huge)
    assert "truncated" in result["content"]


# -------------------------------------------------------------------- issues


async def test_list_issues_excludes_pull_requests_by_default(gh):
    pr_as_issue = {**ISSUE, "number": 8, "pull_request": {"url": "..."}}
    gh.route("GET", "/repos/octo/hello/issues", [ISSUE, pr_as_issue])

    result = await github.github_list_issues({"owner": "octo", "repo": "hello"}, AUTH)
    assert result["count"] == 1
    assert [i["number"] for i in result["issues"]] == [7]
    assert gh.query("state") == "open"

    everything = await github.github_list_issues(
        {"owner": "octo", "repo": "hello", "include_pull_requests": True}, AUTH
    )
    assert [i["number"] for i in everything["issues"]] == [7, 8]


async def test_list_issues_omits_bodies_but_get_issue_keeps_them(gh):
    gh.route("GET", "/repos/octo/hello/issues", [ISSUE])
    gh.route("GET", "/repos/octo/hello/issues/7", ISSUE)

    listed = await github.github_list_issues({"owner": "octo", "repo": "hello"}, AUTH)
    assert "body" not in listed["issues"][0]
    assert listed["issues"][0]["labels"] == ["bug", "p1"]
    assert listed["issues"][0]["assignees"] == ["dev1"]

    single = await github.github_get_issue(
        {"owner": "octo", "repo": "hello", "issue_number": 7}, AUTH
    )
    assert single["body"] == "Traceback goes here"


async def test_long_issue_body_is_clipped(gh):
    gh.route(
        "GET",
        "/repos/octo/hello/issues/7",
        {**ISSUE, "body": "y" * (github.MAX_BODY_CHARS + 1000)},
    )
    result = await github.github_get_issue(
        {"owner": "octo", "repo": "hello", "issue_number": 7}, AUTH
    )
    assert "truncated" in result["body"]


async def test_create_issue_sends_labels_and_assignees(gh):
    gh.route("POST", "/repos/octo/hello/issues", ISSUE, status=201)
    await github.github_create_issue(
        {
            "owner": "octo",
            "repo": "hello",
            "title": "Broken import",
            "labels": ["bug"],
            "assignees": ["dev1"],
        },
        AUTH,
    )
    assert gh.last_body["title"] == "Broken import"
    assert gh.last_body["labels"] == ["bug"]
    assert gh.last_body["assignees"] == ["dev1"]


async def test_create_issue_omits_empty_labels(gh):
    gh.route("POST", "/repos/octo/hello/issues", ISSUE, status=201)
    await github.github_create_issue(
        {"owner": "octo", "repo": "hello", "title": "t"}, AUTH
    )
    assert "labels" not in gh.last_body
    assert "assignees" not in gh.last_body


async def test_update_issue_sends_only_supplied_fields(gh):
    """A PATCH carrying title: None would blank a field nobody asked to change."""
    gh.route("PATCH", "/repos/octo/hello/issues/7", {**ISSUE, "state": "closed"})
    result = await github.github_update_issue(
        {
            "owner": "octo",
            "repo": "hello",
            "issue_number": 7,
            "state": "closed",
            "state_reason": "completed",
        },
        AUTH,
    )
    assert gh.last_body == {"state": "closed", "state_reason": "completed"}
    assert result["state"] == "closed"


async def test_update_issue_with_nothing_to_change_is_rejected(gh):
    result = await github.github_update_issue(
        {"owner": "octo", "repo": "hello", "issue_number": 7}, AUTH
    )
    assert "Nothing to update" in result["error"]
    assert gh.requests == []  # refused locally, no wasted API call


async def test_comment_on_issue(gh):
    gh.route(
        "POST",
        "/repos/octo/hello/issues/7/comments",
        {
            "id": 99,
            "body": "on it",
            "user": {"login": "octo", "type": "User", "html_url": "u"},
            "created_at": "2026-01-03T00:00:00Z",
            "html_url": "https://github.com/octo/hello/issues/7#issuecomment-99",
        },
        status=201,
    )
    result = await github.github_comment_on_issue(
        {"owner": "octo", "repo": "hello", "issue_number": 7, "body": "on it"}, AUTH
    )
    assert gh.last_body == {"body": "on it"}
    assert result["id"] == 99
    assert result["user"]["login"] == "octo"


async def test_list_issue_comments(gh):
    gh.route(
        "GET",
        "/repos/octo/hello/issues/7/comments",
        [{"id": 1, "body": "first", "user": {"login": "a"}, "created_at": "x", "html_url": "u"}],
    )
    result = await github.github_list_issue_comments(
        {"owner": "octo", "repo": "hello", "issue_number": 7}, AUTH
    )
    assert result["count"] == 1
    assert result["comments"][0]["body"] == "first"


async def test_add_labels_returns_resulting_names(gh):
    gh.route(
        "POST",
        "/repos/octo/hello/issues/7/labels",
        [{"name": "bug"}, {"name": "urgent"}],
    )
    result = await github.github_add_labels(
        {"owner": "octo", "repo": "hello", "issue_number": 7, "labels": ["urgent"]}, AUTH
    )
    assert gh.last_body == {"labels": ["urgent"]}
    assert result == {"labels": ["bug", "urgent"], "count": 2}


# ------------------------------------------------------------- pull requests


async def test_list_pull_requests(gh):
    gh.route("GET", "/repos/octo/hello/pulls", [PULL])
    result = await github.github_list_pull_requests(
        {"owner": "octo", "repo": "hello", "state": "all", "base": "main"}, AUTH
    )
    assert gh.query("state") == "all"
    assert gh.query("base") == "main"
    assert result["pull_requests"][0]["head"] == "octo:feature"
    assert "body" not in result["pull_requests"][0]


async def test_get_pull_request_includes_diff_stats(gh):
    gh.route("GET", "/repos/octo/hello/pulls/42", PULL)
    result = await github.github_get_pull_request(
        {"owner": "octo", "repo": "hello", "pull_number": 42}, AUTH
    )
    assert result["body"] == "Speeds things up"
    assert result["additions"] == 10
    assert result["changed_files"] == 1


async def test_create_pull_request(gh):
    gh.route("POST", "/repos/octo/hello/pulls", PULL, status=201)
    result = await github.github_create_pull_request(
        {
            "owner": "octo",
            "repo": "hello",
            "title": "Add caching",
            "head": "feature",
            "base": "main",
        },
        AUTH,
    )
    assert gh.last_body["head"] == "feature"
    assert gh.last_body["base"] == "main"
    assert gh.last_body["draft"] is False
    assert result["number"] == 42


async def test_merge_pull_request_uses_put(gh):
    gh.route(
        "PUT",
        "/repos/octo/hello/pulls/42/merge",
        {"merged": True, "sha": "cafe", "message": "Pull Request successfully merged"},
    )
    result = await github.github_merge_pull_request(
        {"owner": "octo", "repo": "hello", "pull_number": 42, "merge_method": "squash"},
        AUTH,
    )
    assert gh.last.method == "PUT"
    assert gh.last_body == {"merge_method": "squash"}
    assert result == {
        "merged": True,
        "sha": "cafe",
        "message": "Pull Request successfully merged",
    }


async def test_merge_conflict_surfaces_github_message(gh):
    gh.route(
        "PUT",
        "/repos/octo/hello/pulls/42/merge",
        {"message": "Pull Request is not mergeable"},
        status=405,
    )
    result = await github.github_merge_pull_request(
        {"owner": "octo", "repo": "hello", "pull_number": 42}, AUTH
    )
    assert "405" in result["error"]
    assert "not mergeable" in result["error"]


# -------------------------------------------------------------------- search


async def test_search_repos_unwraps_items_and_total(gh):
    gh.route("GET", "/search/repositories", {"total_count": 981, "items": [REPO]})
    result = await github.github_search_repos(
        {"query": "mcp gateway language:python", "sort": "stars"}, AUTH
    )
    assert gh.query("q") == "mcp gateway language:python"
    assert gh.query("sort") == "stars"
    assert result["total_count"] == 981
    assert result["count"] == 1
    assert result["repos"][0]["full_name"] == "octo/hello"


async def test_search_code(gh):
    gh.route(
        "GET",
        "/search/code",
        {
            "total_count": 1,
            "items": [
                {
                    "name": "app.py",
                    "path": "src/app.py",
                    "repository": {"full_name": "octo/hello"},
                    "html_url": "https://github.com/octo/hello/blob/main/src/app.py",
                }
            ],
        },
    )
    result = await github.github_search_code({"query": "AsyncClient repo:octo/hello"}, AUTH)
    assert result["matches"][0]["repository"] == "octo/hello"


async def test_search_issues(gh):
    gh.route("GET", "/search/issues", {"total_count": 3, "items": [ISSUE]})
    result = await github.github_search_issues({"query": "is:open assignee:@me"}, AUTH)
    assert result["total_count"] == 3
    assert result["issues"][0]["number"] == 7


# --------------------------------------------------------------------- stars


async def test_unstar_repo(gh):
    gh.route("DELETE", "/user/starred/octo/hello", None, status=204)
    result = await github.github_unstar_repo({"owner": "octo", "repo": "hello"}, AUTH)
    assert result == {"starred": False, "repo": "octo/hello"}


async def test_star_failure_is_not_reported_as_success(gh):
    gh.route("PUT", "/user/starred/octo/hello", {"message": "Bad credentials"}, status=401)
    result = await github.github_star_repo({"owner": "octo", "repo": "hello"}, AUTH)
    assert "error" in result
    assert "starred" not in result


# ------------------------------------------------------------------ registry


@pytest.fixture(scope="module")
def actions():
    from open_composio.core.registry import ToolRegistry

    registry = ToolRegistry()
    github.register(registry)
    return registry.apps["github"].actions


def test_every_handler_is_registered(actions):
    """A handler nobody registered is dead code an agent can never reach."""
    handlers = {
        name[len("github_"):]
        for name, value in vars(github).items()
        if name.startswith("github_") and callable(value)
    }
    assert handlers == set(actions)


@pytest.mark.parametrize(
    "name",
    [
        "get_user", "list_repos", "get_repo", "create_repo", "list_branches",
        "list_commits", "get_file_contents", "create_issue", "list_issues",
        "get_issue", "update_issue", "comment_on_issue", "list_issue_comments",
        "add_labels", "list_pull_requests", "get_pull_request",
        "create_pull_request", "merge_pull_request", "search_repos",
        "search_code", "search_issues", "star_repo", "unstar_repo",
    ],
)
def test_action_schema_is_well_formed(actions, name):
    schema = actions[name].parameters_schema
    assert schema["description"].endswith(".")
    assert schema["type"] == "object"
    assert "x-destructive" in schema, "destructive-ness must be explicit, not inferred"
    for prop, spec in schema["properties"].items():
        assert "type" in spec, f"{name}.{prop} has no type"
    for req in schema.get("required", []):
        assert req in schema["properties"], f"{name} requires undeclared param {req}"


WRITE_ACTIONS = {
    "create_repo", "create_issue", "update_issue", "comment_on_issue",
    "add_labels", "create_pull_request", "merge_pull_request", "star_repo",
    "unstar_repo",
}


def test_only_write_actions_are_destructive(actions):
    flagged = {name for name, a in actions.items() if a.parameters_schema["x-destructive"]}
    assert flagged == WRITE_ACTIONS


def test_policy_agrees_with_the_schema_flags(actions):
    """The executor asks policy.is_destructive, not the schema — they must match."""
    for name, action in actions.items():
        assert is_destructive(name, action.parameters_schema) == (name in WRITE_ACTIONS), name


def test_read_actions_are_cached_and_writes_are_not(actions):
    for name, action in actions.items():
        ttl = action.parameters_schema.get("x-cache-ttl", 0)
        if name in WRITE_ACTIONS:
            assert not ttl, f"{name} mutates state and must not be cached"
        else:
            assert ttl > 0, f"{name} is a read and should set x-cache-ttl"


def test_exactly_one_verify_probe(actions):
    probes = [n for n, a in actions.items() if a.parameters_schema.get("x-verify")]
    assert probes == ["get_user"]


# --------------------------------------------------------------- integration

# The tests above call handlers directly, which skips schema validation, policy
# and caching. These drive the real executor, so the schemas themselves are
# under test rather than just the request-building.


@pytest.fixture
def connected(tmp_path, gh):
    from open_composio import OpenComposio
    from open_composio.core.vault import EncryptedFileVault

    oc = OpenComposio(
        vault=EncryptedFileVault(directory=tmp_path), audit=False, load_upstreams=False
    )
    oc.connect("github", token="ghp_test")
    return oc


async def test_action_runs_through_the_executor(connected, gh):
    gh.route("GET", "/user/repos", [REPO])
    result = await connected.aexecute(
        "github", "list_repos", {"per_page": 5, "sort": "updated"}
    )
    assert result["repos"][0]["full_name"] == "octo/hello"


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"sort": "nonsense"}, "nonsense"),  # enum violation
        ({"reponame": "typo"}, "reponame"),  # hallucinated field name
        ({"per_page": 500}, "100"),  # above GitHub's ceiling
    ],
)
async def test_schemas_reject_bad_params(connected, gh, params, expected):
    from open_composio.core.executor import ValidationError

    with pytest.raises(ValidationError, match=expected):
        await connected.aexecute("github", "list_repos", params)
    assert gh.requests == []  # rejected before any API call was spent


async def test_required_params_are_enforced(connected, gh):
    from open_composio.core.executor import ValidationError

    with pytest.raises(ValidationError, match="issue_number"):
        await connected.aexecute("github", "get_issue", {"owner": "o", "repo": "r"})


async def test_destructive_action_needs_approval(connected, gh):
    """The x-destructive flags are what stop an agent silently merging a PR."""
    from open_composio.core.policy import ApprovalRequired, Policy

    connected.executor.policy = Policy()
    gh.route("GET", "/repos/octo/hello", REPO)

    await connected.aexecute("github", "get_repo", {"owner": "octo", "repo": "hello"})

    with pytest.raises(ApprovalRequired):
        await connected.aexecute(
            "github", "merge_pull_request", {"owner": "octo", "repo": "hello", "pull_number": 42}
        )


async def test_read_action_is_cached(connected, gh):
    gh.route("GET", "/repos/octo/hello", REPO)
    for _ in range(3):
        await connected.aexecute("github", "get_repo", {"owner": "octo", "repo": "hello"})
    assert len(gh.requests) == 1
