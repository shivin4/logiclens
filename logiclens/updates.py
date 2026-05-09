"""Check GitHub Releases for newer LogicLens builds (no auto-download)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from logiclens.version import (
    GITHUB_REPO_NAME,
    GITHUB_REPO_OWNER,
    __version__,
)


def _version_tuple(v: str) -> tuple[int, ...]:
    s = (v or "").strip().lstrip("vV")
    parts: list[int] = []
    for segment in s.split("."):
        num = ""
        for ch in segment:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def check_for_updates(timeout_sec: float = 12.0) -> dict:
    """
    Query https://api.github.com/repos/.../releases/latest .
    Returns a dict safe to JSON-ify; on failure, ``error`` is set.
    """
    url = (
        f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/"
        f"{GITHUB_REPO_NAME}/releases/latest"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "LogicLens-UpdateCheck",
        },
        method="GET",
    )
    current = __version__
    out: dict = {
        "current_version": current,
        "latest_version": current,
        "update_available": False,
        "release_url": f"https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest",
        "installer_asset_url": None,
        "body_preview": None,
    }
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        out["error"] = str(e)
        return out
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
        return out

    tag = (data.get("tag_name") or "").strip()
    latest = tag.lstrip("vV") if tag else ""
    if not latest:
        latest = (data.get("name") or "").strip().lstrip("vV")

    out["latest_version"] = latest or out["latest_version"]
    out["release_url"] = data.get("html_url") or out["release_url"]
    body = data.get("body")
    if isinstance(body, str) and body.strip():
        out["body_preview"] = body.strip()[:280] + ("…" if len(body) > 280 else "")

    assets = data.get("assets") or []
    for a in assets:
        if not isinstance(a, dict):
            continue
        name = (a.get("name") or "").lower()
        dl = a.get("browser_download_url")
        if name.endswith(".exe") and "setup" in name and dl:
            out["installer_asset_url"] = dl
            break
    if not out["installer_asset_url"]:
        for a in assets:
            if not isinstance(a, dict):
                continue
            name = (a.get("name") or "").lower()
            dl = a.get("browser_download_url")
            if name.endswith(".exe") and dl:
                out["installer_asset_url"] = dl
                break

    try:
        out["update_available"] = _version_tuple(latest) > _version_tuple(current)
    except Exception:
        out["update_available"] = latest != current and bool(
            re.match(r"^\d+\.\d+\.\d+", latest)
        )

    return out
