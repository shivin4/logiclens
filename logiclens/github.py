"""GitHub OAuth (device flow), repo listing, and remote source sync without full clone."""

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any

# Draft for direct GitHub URL importing

_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)",
    re.IGNORECASE,
)

def parse_github_url(url: str) -> dict[str, str] | None:
    s = (url or "").strip().strip('"').strip("'").rstrip("/")
    if not s:
        return None
    m = _GITHUB_URL_RE.match(s)
    if not m:
        return None
    branch = (m.group("branch") or "").split("/")[0] if m.group("branch") else ""
    return {"owner": m.group("owner"), "repo": m.group("repo").removesuffix(".git"), "ref": branch}

from logiclens.config import get_data_dir

def auth_store_path() -> Path:
    return get_data_dir() / "github_auth.json"

GITHUB_API = "https://api.github.com"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_DEVICE_TOKEN_URL = "https://github.com/login/oauth/access_token"

def start_device_flow(client_id: str | None = None) -> dict[str, Any]:
    from logiclens.config import github_client_id
    cid = (client_id or github_client_id()).strip()
    if not cid:
        raise ValueError("GitHub OAuth Client ID is not configured.")
    data = urllib.parse.urlencode({"client_id": cid, "scope": "repo read:user"}).encode("utf-8")
    req = urllib.request.Request(GITHUB_DEVICE_CODE_URL, data=data, headers={"Accept": "application/json", "User-Agent": "LogicLens-Desktop"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def poll_device_flow(device_code: str, client_id: str | None = None) -> dict[str, Any]:
    from logiclens.config import github_client_id
    cid = (client_id or github_client_id()).strip()
    data = urllib.parse.urlencode({
        "client_id": cid,
        "device_code": device_code.strip(),
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }).encode("utf-8")
    req = urllib.request.Request(GITHUB_DEVICE_TOKEN_URL, data=data, headers={"Accept": "application/json", "User-Agent": "LogicLens-Desktop"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def load_github_token() -> str | None:
    p = auth_store_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return (data.get("access_token") or "").strip() or None
    except Exception:
        return None

def save_github_session(payload: dict[str, Any]) -> None:
    p = auth_store_path()
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
