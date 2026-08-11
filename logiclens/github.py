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
