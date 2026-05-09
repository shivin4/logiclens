"""Central paths and environment loading for LogicLens."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def normalize_project_file_path(path: str) -> str:
    """Stable key for manifests, Chroma metadata filepath, and SQLite node.file."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path))).replace("\\", "/")


def get_data_dir() -> Path:
    """User-writable directory for graph DB, Chroma, and local .env."""
    override = os.environ.get("LOGICLENS_DATA_DIR")
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    if is_packaged():
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LogicLens"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(__file__).resolve().parent.parent


def load_app_env() -> None:
    """Load secrets from data dir first, then process cwd (dev)."""
    data = get_data_dir()
    env_in_data = data / ".env"
    if env_in_data.is_file():
        load_dotenv(env_in_data)
    load_dotenv()


def graph_db_path() -> Path:
    return get_data_dir() / "logiclens_graph.db"


def chroma_dir() -> Path:
    p = get_data_dir() / "chroma_data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def chroma_collection_name() -> str:
    """Legacy default / env override (CLI tools). Prefer chroma_collection_for_project for app flows."""
    return os.environ.get("CHROMA_COLLECTION_NAME", "codebase_nodes")


def chroma_collection_for_project(project_root: str | None) -> str:
    """
    One Chroma collection per analyzed folder so embeddings survive re-opening a project
    and different projects do not overwrite each other.
    """
    if not project_root or not str(project_root).strip():
        return chroma_collection_name()
    norm = os.path.normcase(os.path.abspath(os.path.normpath(str(project_root))))
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return f"ll_proj_{digest}"


def flask_host() -> str:
    return os.environ.get("FLASK_HOST", "127.0.0.1")


def flask_port() -> int:
    return int(os.environ.get("FLASK_PORT", "5000"))


def ensure_flask_port_allocated() -> int:
    """
    First free TCP port from FLASK_PORT (default 5000), up to 64 tries.
    Sets os.environ['FLASK_PORT'] for Waitress and the webview URL.
    Avoids port clashes when opening multiple desktop windows.
    """
    import socket

    host = flask_host()
    base = int(os.environ.get("FLASK_PORT", "5000"))
    for port in range(base, base + 64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            os.environ["FLASK_PORT"] = str(port)
            return port
    hi = base + 63
    raise RuntimeError(
        f"Could not bind LogicLens server on {host} ports {base}-{hi}."
    )


def use_debug_server() -> bool:
    return os.environ.get("LOGICLENS_DEBUG", "").lower() in ("1", "true", "yes")
