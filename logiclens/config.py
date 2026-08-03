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


def apply_data_dir_override(path: str | Path) -> Path:
    """Switch active storage folder for this process (no restart required)."""
    resolved = Path(path).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    os.environ["LOGICLENS_DATA_DIR"] = str(resolved)
    return resolved


def load_app_env() -> None:
    """Load secrets from data dir first, then process cwd (dev)."""
    data = get_data_dir()
    env_in_data = data / ".env"
    if env_in_data.is_file():
        load_dotenv(env_in_data)
    load_dotenv()
    _apply_packaged_chroma_defaults()


def _apply_packaged_chroma_defaults() -> None:
    """PyInstaller: Chroma's default telemetry loads PostHog, which is easy to miss in
    the frozen bundle. Rust bindings still need ``chromadb_rust_bindings`` in the spec.

    Override with ``CHROMA_PRODUCT_TELEMETRY_IMPL`` in .env if needed.
    """
    if not is_packaged():
        return
    os.environ.setdefault(
        "CHROMA_PRODUCT_TELEMETRY_IMPL",
        "logiclens.chroma_noop_telemetry.NoopProductTelemetry",
    )
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")


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


def llm_provider() -> str:
    return os.environ.get("LLM_PROVIDER", "gemini").lower()


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")


def gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "")


def groq_api_key() -> str:
    return os.environ.get("GROQ_API_KEY", "")


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "llama3:latest")


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def active_ai_provider() -> str:
    prov = llm_provider()
    if prov in ("ollama", "local"):
        return "ollama"
    if prov == "gemini":
        return "gemini"
    if prov == "groq":
        return "groq"
    # Fallback logic for legacy 'cloud' setting
    if gemini_api_key():
        return "gemini"
    if groq_api_key():
        return "groq"
    return "gemini"


