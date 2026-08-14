"""Application version and update source (GitHub Releases)."""

from __future__ import annotations

import os

# Bump for each shipped build; keep in sync with packaging/installer.iss (#define MyAppVersion).
_DEFAULT_VERSION = "1.1.0"

__version__ = (os.environ.get("LOGICLENS_APP_VERSION") or _DEFAULT_VERSION).strip() or _DEFAULT_VERSION

GITHUB_REPO_OWNER = os.environ.get("LOGICLENS_GITHUB_OWNER", "shivin4").strip() or "shivin4"
GITHUB_REPO_NAME = os.environ.get("LOGICLENS_GITHUB_REPO", "logiclens").strip() or "logiclens"

# Shipped with the desktop app — users do NOT create their own OAuth app.
# GitHub Device Flow uses only the public Client ID (no secret in the app).
# Override via GITHUB_CLIENT_ID in .env for forks or staging OAuth apps.
DEFAULT_GITHUB_OAUTH_CLIENT_ID = "Ov23liX07ClHNuU6c0Nc"
