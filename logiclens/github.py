"""GitHub OAuth (device flow), repo listing, and remote source sync without full clone."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from logiclens.config import get_data_dir
from logiclens.scan_ignore import CANDIDATE_SOURCE_EXTENSIONS, should_skip_github_path

GITHUB_API = "https://api.github.com"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_DEVICE_TOKEN_URL = "https://github.com/login/oauth/access_token"

_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)(?:/tree/(?P<branch>[\w./-]+))?",
    re.IGNORECASE,
)


def auth_store_path() -> Path:
    return get_data_dir() / "github_auth.json"


def parse_github_url(url: str) -> dict[str, str] | None:
    """Parse github.com/owner/repo[/tree/branch] URLs."""
    s = (url or "").strip().strip('"').strip("'").rstrip("/")
    if not s:
        return None
    m = _GITHUB_URL_RE.match(s)
    if not m:
        return None
    owner = m.group("owner")
    repo = m.group("repo").removesuffix(".git")
    branch = (m.group("branch") or "").split("/")[0] if m.group("branch") else ""
    return {"owner": owner, "repo": repo, "ref": branch}


def load_github_token() -> str | None:
    p = auth_store_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        token = (data.get("access_token") or "").strip()
        return token or None
    except (OSError, json.JSONDecodeError):
        return None


def save_github_session(payload: dict[str, Any]) -> None:
    p = auth_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_github_session() -> None:
    p = auth_store_path()
    if p.is_file():
        try:
            p.unlink()
        except OSError:
            pass


def github_user_profile() -> dict[str, Any] | None:
    token = load_github_token()
    if not token:
        return None
    try:
        return _github_request("GET", "/user", token=token)
    except Exception:
        return None


def github_client_id() -> str:
    from logiclens.version import DEFAULT_GITHUB_OAUTH_CLIENT_ID

    return (
        os.environ.get("GITHUB_CLIENT_ID") or DEFAULT_GITHUB_OAUTH_CLIENT_ID or ""
    ).strip()


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or load_github_token()

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        h = {
            "Accept": accept,
            "User-Agent": "LogicLens-Desktop",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def request(self, method: str, path: str, data: dict | None = None) -> Any:
        return _github_request(method, path, token=self.token, data=data)

    def default_branch(self, owner: str, repo: str) -> str:
        info = self.request("GET", f"/repos/{owner}/{repo}")
        return str(info.get("default_branch") or "main")

    def list_repos(self, page: int = 1, per_page: int = 30) -> list[dict[str, Any]]:
        if not self.token:
            return []
        rows = self.request(
            "GET",
            f"/user/repos?sort=updated&per_page={per_page}&page={page}&affiliation=owner,collaborator,organization_member",
        )
        out: list[dict[str, Any]] = []
        for r in rows if isinstance(rows, list) else []:
            out.append(
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "full_name": r.get("full_name"),
                    "private": bool(r.get("private")),
                    "html_url": r.get("html_url"),
                    "default_branch": r.get("default_branch") or "main",
                    "language": r.get("language"),
                    "updated_at": r.get("updated_at"),
                }
            )
        return out

    def tree_blobs(self, owner: str, repo: str, ref: str) -> list[str]:
        """Return blob paths under ref (recursive tree)."""
        resolved = ref
        if not resolved:
            resolved = self.default_branch(owner, repo)
        tree = self.request(
            "GET",
            f"/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(resolved, safe='')}?recursive=1",
        )
        paths: list[str] = []
        for item in tree.get("tree") or []:
            if item.get("type") != "blob":
                continue
            path = item.get("path") or ""
            ext = os.path.splitext(path)[1].lower()
            if ext not in CANDIDATE_SOURCE_EXTENSIONS:
                continue
            if should_skip_github_path(path):
                continue
            paths.append(path)
        return paths

    def file_text(self, owner: str, repo: str, path: str, ref: str) -> str:
        data = _github_request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(path, safe='/')}?ref={urllib.parse.quote(ref, safe='')}",
            token=self.token,
            accept="application/vnd.github.raw",
        )
        if isinstance(data, str):
            return data
        if isinstance(data, dict) and data.get("encoding") == "base64":
            import base64

            raw = data.get("content") or ""
            return base64.b64decode(raw).decode("utf-8", errors="ignore")
        return ""


def cache_dir_for_repo(owner: str, repo: str) -> Path:
    return get_data_dir() / "github_cache" / owner / repo


def sync_repo_to_cache(
    owner: str,
    repo: str,
    ref: str | None = None,
    token: str | None = None,
    max_files: int = 400,
) -> str:
    """
    Fetch parseable source files via GitHub API into a local cache directory.
    Returns the cache root path suitable for analyze_project().
    """
    client = GitHubClient(token)
    branch = ref or client.default_branch(owner, repo)
    paths = client.tree_blobs(owner, repo, branch)
    if len(paths) > max_files:
        paths = paths[:max_files]

    root = cache_dir_for_repo(owner, repo)
    if root.is_dir():
        for child in root.iterdir():
            if child.name == ".github_source.json":
                continue
            if child.is_dir():
                import shutil

                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass
    root.mkdir(parents=True, exist_ok=True)

    fetched = 0
    for rel in paths:
        try:
            text = client.file_text(owner, repo, rel, branch)
        except Exception as exc:
            print(f"[GitHub] Skip {rel}: {exc}")
            continue
        if len(text) > 800_000:
            continue
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", errors="ignore")
        fetched += 1

    meta = {
        "owner": owner,
        "repo": repo,
        "ref": branch,
        "source": "github_api",
        "fetched_files": fetched,
        "synced_at": int(time.time()),
    }
    (root / ".github_source.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if fetched == 0:
        raise RuntimeError(
            f"No supported source files fetched from {owner}/{repo}. "
            "The repo may be empty, private without auth, or above the file limit."
        )
    return str(root)


def start_device_flow() -> dict[str, Any]:
    client_id = github_client_id()
    if not client_id:
        raise RuntimeError(
            "GitHub sign-in is not available in this build. "
            "Contact the LogicLens maintainer or set GITHUB_CLIENT_ID for a custom build."
        )
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "scope": "repo read:user",
        }
    ).encode("utf-8")
    data = _http_json("POST", GITHUB_DEVICE_CODE_URL, data=payload, headers={"Accept": "application/json"})
    return {
        "device_code": data.get("device_code"),
        "user_code": data.get("user_code"),
        "verification_uri": data.get("verification_uri") or "https://github.com/login/device",
        "expires_in": int(data.get("expires_in") or 900),
        "interval": int(data.get("interval") or 5),
    }


def poll_device_flow(device_code: str) -> dict[str, Any]:
    client_id = github_client_id()
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GITHUB_DEVICE_TOKEN_URL,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/json",
            "User-Agent": "LogicLens-Desktop",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            data = json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return {"status": "error", "error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    err = (data.get("error") or "").strip()
    if err in ("authorization_pending", "slow_down"):
        return {"status": "pending", "error": err, "interval": data.get("interval")}

    if err:
        if err in ("expired_token", "token_expired"):
            return {"status": "expired", "error": err}
        return {"status": "error", "error": err, "description": data.get("error_description")}

    token = (data.get("access_token") or "").strip()
    if not token:
        return {"status": "error", "error": "no_token"}

    try:
        profile = GitHubClient(token).request("GET", "/user")
    except Exception as exc:
        return {"status": "error", "error": f"Could not load GitHub profile: {exc}"}

    save_github_session(
        {
            "access_token": token,
            "login": profile.get("login"),
            "name": profile.get("name") or profile.get("login"),
            "avatar_url": profile.get("avatar_url"),
            "saved_at": int(time.time()),
        }
    )
    return {
        "status": "ok",
        "login": profile.get("login"),
        "name": profile.get("name") or profile.get("login"),
        "avatar_url": profile.get("avatar_url"),
    }


def _github_request(
    method: str,
    path: str,
    token: str | None = None,
    data: dict | None = None,
    accept: str = "application/vnd.github+json",
) -> Any:
    headers = {
        "Accept": accept,
        "User-Agent": "LogicLens-Desktop",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    url = path if path.startswith("http") else f"{GITHUB_API}{path}"
    return _http_json(method, url, data=body, headers=headers)


def _http_json(
    method: str,
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            if not raw:
                return {}
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return json.loads(raw.decode("utf-8"))
            return raw.decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        detail_str = exc.read().decode("utf-8", errors="ignore")
        msg = f"HTTP {exc.code}"
        if detail_str:
            try:
                parsed = json.loads(detail_str)
                if isinstance(parsed, dict) and parsed.get("message"):
                    msg = f"GitHub API Error ({exc.code}): {parsed['message']}"
                else:
                    msg = detail_str
            except Exception:
                msg = detail_str
        if exc.code == 403 and ("rate limit" in msg.lower() or "forbidden" in msg.lower()):
            msg += " — Try signing in with GitHub in Settings for higher rate limits."
        elif exc.code == 404:
            msg = f"GitHub repository not found or private (HTTP 404). Check the owner and repo name."
        raise RuntimeError(msg) from exc
