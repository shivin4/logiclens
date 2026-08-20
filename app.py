from logiclens.config import (
    active_ai_provider,
    apply_data_dir_override,
    chroma_collection_for_project,
    chroma_dir,
    flask_host,
    flask_port,
    gemini_api_key,
    gemini_model,
    get_data_dir,
    graph_db_path,
    groq_api_key,
    llm_provider,
    load_app_env,
    normalize_project_file_path,
    ollama_host,
    ollama_model,
    use_debug_server,
)

load_app_env()

import json
import os
import subprocess
import sys
import git
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response,
    send_from_directory,
    stream_with_context,
)
from extractor import analyze_project, LANGUAGES, CONFIGS, _load_analysis_manifest, _manifest_path_for_project
import tree_sitter
from tree_sitter import Parser, Query
import chromadb

from pathlib import Path

from logiclens.sqlite_graph import SqliteGraphStore
from logiclens.telemetry import init_flask_telemetry
from logiclens.updates import check_for_updates
from logiclens.version import __version__
from logiclens import github as github_api

app = Flask(__name__, static_folder="static", static_url_path="/static")
init_flask_telemetry(app)

current_repo_path = None
_analyze_jobs: dict[str, dict] = {}
_analyze_jobs_lock = threading.Lock()


def _parse_env_file(env_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not env_path.is_file():
        return out
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    return out


def _graph() -> SqliteGraphStore:
    return SqliteGraphStore(graph_db_path())


@app.route("/logo/<path:filename>")
def serve_logo(filename: str):
    return send_from_directory("logo", filename)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/setup", methods=["GET"])
def setup_page():
    return render_template(
        "setup.html",
        data_dir=str(get_data_dir()),
        current_provider=active_ai_provider(),
        gemini_model=gemini_model(),
        ollama_model=ollama_model(),
        ollama_host=ollama_host(),
        has_gemini_key=bool(gemini_api_key()),
        has_groq_key=bool(groq_api_key()),
    )


@app.route("/onboarding", methods=["GET"])
def onboarding_page():
    return render_template("onboarding.html", data_dir=str(get_data_dir()))


@app.route("/setup", methods=["POST"])
def setup_save():
    data = request.get_json() or {}
    llm_prov = (data.get("llm_provider") or "gemini").strip().lower()
    groq = (data.get("groq_api_key") or "").strip()
    gemini = (data.get("gemini_api_key") or "").strip()
    gemini_mod = (data.get("gemini_model") or "gemini-3.5-flash-lite").strip()
    ollama_mod = (data.get("ollama_model") or "llama3:latest").strip()
    ollama_hst = (data.get("ollama_host") or "http://localhost:11434").strip()

    data_dir = get_data_dir()
    env_path = data_dir / ".env"
    prev = _parse_env_file(env_path)

    if llm_prov == "gemini":
        if not gemini and not prev.get("GEMINI_API_KEY"):
            return jsonify({"error": "GEMINI_API_KEY is required for Gemini AI provider."}), 400
    elif llm_prov == "groq":
        if not groq and not prev.get("GROQ_API_KEY"):
            return jsonify({"error": "GROQ_API_KEY is required for Groq AI provider."}), 400
    elif llm_prov == "cloud":
        if gemini or prev.get("GEMINI_API_KEY"):
            llm_prov = "gemini"
        elif groq or prev.get("GROQ_API_KEY"):
            llm_prov = "groq"
        else:
            return jsonify({"error": "API Key is required for Cloud AI mode (Gemini or Groq)."}), 400

    telemetry_on = bool(data.get("telemetry_opt_in"))
    rows: dict[str, str] = {
        "LLM_PROVIDER": llm_prov,
        "GEMINI_MODEL": gemini_mod or prev.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        "GEMINI_API_KEY": gemini or prev.get("GEMINI_API_KEY", ""),
        "GROQ_API_KEY": groq or prev.get("GROQ_API_KEY", ""),
        "OLLAMA_MODEL": ollama_mod or prev.get("OLLAMA_MODEL", "llama3:latest"),
        "OLLAMA_HOST": ollama_hst or prev.get("OLLAMA_HOST", "http://localhost:11434"),
        "CHROMA_PERSIST_PATH": str(chroma_dir().resolve()),
        "LOGICLENS_TELEMETRY": "1" if telemetry_on else "0",
    }
    sentry_dsn = (data.get("sentry_dsn") or "").strip()
    if sentry_dsn:
        rows["SENTRY_DSN"] = sentry_dsn
    elif prev.get("SENTRY_DSN"):
        rows["SENTRY_DSN"] = prev["SENTRY_DSN"]
    gh_client = (data.get("github_client_id") or prev.get("GITHUB_CLIENT_ID") or "").strip()
    if gh_client:
        rows["GITHUB_CLIENT_ID"] = gh_client

    key_order = [
        "LLM_PROVIDER",
        "GEMINI_MODEL",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OLLAMA_MODEL",
        "OLLAMA_HOST",
        "CHROMA_PERSIST_PATH",
        "LOGICLENS_TELEMETRY",
        "SENTRY_DSN",
        "GITHUB_CLIENT_ID",
    ]
    lines = [f"{k}={rows[k]}" for k in key_order if k in rows] + [""]
    env_path.write_text("\n".join(lines), encoding="utf-8")
    
    # Update active os.environ immediately
    for k, v in rows.items():
        if v:
            os.environ[k] = v
    load_app_env()

    return jsonify(
        {
            "status": "success",
            "message": "Saved. AI Settings have been updated.",
            "active_provider": active_ai_provider(),
        }
    )


@app.route("/api/ollama/models", methods=["GET"])
def api_ollama_models():
    """Fetch installed Ollama models from local daemon."""
    import urllib.request
    from logiclens.config import ollama_host
    host = ollama_host()
    url = f"{host}/api/tags"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LogicLens/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in body.get("models", []) if m.get("name")]
                return jsonify({"status": "ok", "models": models, "host": host})
    except Exception as e:
        return jsonify({"status": "offline", "models": [], "error": str(e), "host": host})
    return jsonify({"status": "offline", "models": [], "host": host})



def _indexed_projects_from_manifests() -> list[dict]:
    """Projects that have an on-disk analysis manifest (for settings / cleanup UI)."""
    data_dir = get_data_dir()
    out: list[dict] = []
    for p in sorted(data_dir.glob("analysis_manifest_*.json")):
        key = p.name[len("analysis_manifest_") : -len(".json")]
        try:
            manifest = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        root = manifest.get("root")
        if not root or not isinstance(root, str):
            continue
        files = manifest.get("files")
        nfiles = len(files) if isinstance(files, dict) else 0
        out.append(
            {
                "project_key": key,
                "root": root,
                "tracked_files": nfiles,
            }
        )
    out.sort(key=lambda x: x["root"].lower())
    return out


@app.route("/api/bootstrap", methods=["GET"])
def api_bootstrap():
    global current_repo_path
    if current_repo_path is None:
        last_root = _read_last_graph_root_file()
        if last_root and os.path.isdir(last_root):
            current_repo_path = last_root

    data_dir = get_data_dir()
    env_file = data_dir / ".env"
    prov = active_ai_provider()
    g_key = gemini_api_key()
    q_key = groq_api_key()
    is_conf = (
        (prov == "gemini" and bool(g_key))
        or (prov == "groq" and bool(q_key))
        or (prov == "ollama")
    )
    return jsonify(
        {
            "data_dir": str(data_dir),
            "graph_db": str(graph_db_path()),
            "user_env_present": env_file.is_file(),
            "groq_configured": bool(q_key),
            "gemini_configured": bool(g_key),
            "ai_config": {
                "provider": prov,
                "provider_name": "Gemini" if prov == "gemini" else ("Ollama" if prov == "ollama" else "Groq"),
                "gemini_model": gemini_model(),
                "gemini_configured": bool(g_key),
                "groq_configured": bool(q_key),
                "ollama_host": ollama_host(),
                "ollama_model": ollama_model(),
                "is_configured": is_conf,
            },
            "app_version": __version__,
            "telemetry_enabled": os.environ.get("LOGICLENS_TELEMETRY", "").lower()
            in ("1", "true", "yes")
            and bool((os.environ.get("SENTRY_DSN") or "").strip()),
            "indexed_projects": _indexed_projects_from_manifests(),
            "current_repo_path": current_repo_path,
            "github_client_configured": bool(github_api.github_client_id()),
            "github_user": github_api.github_user_profile(),
        }
    )


@app.route("/api/updates/check", methods=["GET"])
def api_updates_check():
    return jsonify(check_for_updates())


@app.route("/api/health", methods=["GET"])
def api_health():
    try:
        g = _graph()
        n = g.node_count()
        return jsonify({"status": "ok", "node_count": n})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


def _is_localhost_request() -> bool:
    addr = (request.remote_addr or "").replace("::ffff:", "")
    return addr in ("127.0.0.1", "::1", "localhost")


RECENT_FOLDERS_MAX = 10
_RECENT_FOLDERS_FILE = "recent_folders.json"


def _recent_folders_path() -> Path:
    return get_data_dir() / _RECENT_FOLDERS_FILE


def _recent_folder_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path.strip())))


def _read_recent_folders() -> list[str]:
    path = _recent_folders_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict) and isinstance(data.get("folders"), list):
        raw = data["folders"]
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        p = str(item).strip()
        if not p:
            continue
        k = _recent_folder_key(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
        if len(out) >= RECENT_FOLDERS_MAX:
            break
    return out


def _write_recent_folders(folders: list[str]) -> None:
    path = _recent_folders_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"folders": folders}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _add_recent_folder(folder: str) -> list[str]:
    p = folder.strip().strip('"').strip("'")
    if not p:
        return _read_recent_folders()
    cur = _read_recent_folders()
    nk = _recent_folder_key(p)
    cur = [x for x in cur if _recent_folder_key(x) != nk]
    cur.insert(0, p)
    cur = cur[:RECENT_FOLDERS_MAX]
    _write_recent_folders(cur)
    return cur


_USER_PREFS_FILE = "user_prefs.json"


def _user_prefs_path() -> Path:
    return get_data_dir() / _USER_PREFS_FILE


def _read_user_prefs() -> dict:
    path = _user_prefs_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_user_prefs(prefs: dict) -> None:
    path = _user_prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(prefs, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _merge_recent_folder_lists(import_first: list[str], existing: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in (import_first, existing):
        for item in group:
            p = str(item).strip()
            if not p:
                continue
            k = _recent_folder_key(p)
            if k in seen:
                continue
            seen.add(k)
            out.append(p)
            if len(out) >= RECENT_FOLDERS_MAX:
                return out
    return out


@app.route("/api/recent_folders", methods=["GET"])
def api_recent_folders_get():
    """Recent project paths shared across all LogicLens windows (same data dir)."""
    if not _is_localhost_request():
        return jsonify({"error": "Local use only."}), 403
    return jsonify({"folders": _read_recent_folders()})


@app.route("/api/recent_folders", methods=["POST"])
def api_recent_folders_post():
    if not _is_localhost_request():
        return jsonify({"error": "Local use only."}), 403
    data = request.get_json() or {}
    if data.get("path"):
        folders = _add_recent_folder(str(data["path"]))
        return jsonify({"folders": folders})
    imp = data.get("import_folders")
    if isinstance(imp, list) and imp:
        merged = _merge_recent_folder_lists(
            [str(x) for x in imp],
            _read_recent_folders(),
        )
        _write_recent_folders(merged)
        return jsonify({"folders": merged})
    return jsonify({"error": 'Expected "path" or non-empty "import_folders".'}), 400


@app.route("/api/user/prefs", methods=["GET"])
def api_user_prefs_get():
    """User preferences persisted in the LogicLens data directory."""
    if not _is_localhost_request():
        return jsonify({"error": "Local use only."}), 403
    return jsonify(_read_user_prefs())


@app.route("/api/user/prefs/tour", methods=["POST"])
def api_user_prefs_tour_dismiss():
    """Mark the first-run product tour as dismissed (skip, done, or already shown)."""
    if not _is_localhost_request():
        return jsonify({"error": "Local use only."}), 403
    prefs = _read_user_prefs()
    prefs["tour_dismissed"] = True
    _write_user_prefs(prefs)
    return jsonify({"status": "ok", "tour_dismissed": True})


_ANALYZE_HISTORY_FILE = "analyze_history.json"
_ANALYZE_HISTORY_MAX = 10


def _analyze_history_path() -> Path:
    return get_data_dir() / _ANALYZE_HISTORY_FILE


def _read_analyze_history() -> list[dict]:
    path = _analyze_history_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _append_analyze_history(entry: dict) -> None:
    path = _analyze_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    hist = _read_analyze_history()
    record = dict(entry)
    record.setdefault("id", uuid.uuid4().hex[:12])
    record.setdefault("at", datetime.now(timezone.utc).isoformat())
    hist.insert(0, record)
    hist = hist[:_ANALYZE_HISTORY_MAX]
    path.write_text(json.dumps(hist, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _record_analyze_history(
    directory_path: str,
    *,
    status: str,
    result: dict | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    payload: dict = {
        "path": directory_path,
        "status": status,
        "duration_ms": duration_ms,
    }
    if error:
        payload["error"] = error
    if result:
        payload["files"] = result.get("files")
        payload["functions"] = result.get("functions")
        payload["skipped_unchanged"] = bool(result.get("skipped_unchanged"))
        payload["incremental"] = bool(result.get("incremental"))
    _append_analyze_history(payload)


def _security_findings_summary() -> dict:
    try:
        store = _graph()
        nodes_dict, _ = store.fetch_full_graph()
        findings = []
        for node in nodes_dict.values():
            vulns = node.get("vulnerabilities") or []
            if not vulns:
                continue
            findings.append(
                {
                    "id": node.get("id"),
                    "label": node.get("label") or node.get("name"),
                    "file": node.get("file"),
                    "count": len(vulns),
                }
            )
        return {"count": len(findings), "items": findings[:25]}
    except Exception:
        return {"count": 0, "items": []}


def _git_status_summary() -> dict:
    if not current_repo_path:
        return {"is_git_repo": False, "is_dirty": False, "branch": None}
    try:
        repo = git.Repo(current_repo_path, search_parent_directories=True)
        branch = None
        try:
            branch = repo.active_branch.name
        except Exception:
            branch = "HEAD"
        return {
            "is_git_repo": True,
            "is_dirty": bool(repo.is_dirty(untracked_files=True)),
            "branch": branch,
        }
    except Exception:
        pass

    meta_file = os.path.join(current_repo_path, ".github_source.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return {
                "is_git_repo": True,
                "is_dirty": False,
                "branch": meta.get("ref") or "main",
            }
        except Exception:
            pass

    return {"is_git_repo": False, "is_dirty": False, "branch": None}


def _index_stale_summary() -> dict:
    if not current_repo_path or not os.path.isdir(current_repo_path):
        return {"stale": False, "reason": None}
    project_key = chroma_collection_for_project(current_repo_path)
    manifest_path = _manifest_path_for_project(project_key)
    if not manifest_path.is_file():
        return {"stale": False, "reason": None}
    try:
        manifest_mtime = manifest_path.stat().st_mtime
        manifest = _load_analysis_manifest(manifest_path) or {}
        files = manifest.get("files") or {}
        root = manifest.get("root") or current_repo_path
        checked = 0
        for rel in files:
            if checked >= 300:
                break
            checked += 1
            fp = os.path.join(root, rel.replace("/", os.sep))
            if os.path.isfile(fp) and os.path.getmtime(fp) > manifest_mtime:
                return {"stale": True, "reason": "Source files changed since last analyze."}
        return {"stale": False, "reason": None}
    except Exception:
        return {"stale": False, "reason": None}


def _notifications_dismissed_map(prefs: dict) -> dict:
    dismissed = prefs.get("notifications_dismissed")
    if isinstance(dismissed, dict):
        return dismissed
    legacy = prefs.get("notifications_dismissed_ids")
    if isinstance(legacy, list):
        return {str(item_id): True for item_id in legacy if item_id}
    return {}


def _notification_is_dismissed(dismissed: dict, notif_id: str, state_key: str | None = None) -> bool:
    stored = dismissed.get(notif_id)
    if stored is None:
        return False
    if state_key is None:
        return stored is True or stored == "1"
    return stored == state_key


def _remove_analyze_history_entries(entry_ids: list[str]) -> None:
    if not entry_ids:
        return
    remove = {str(x) for x in entry_ids if x}
    hist = _read_analyze_history()
    filtered = [h for h in hist if str(h.get("id") or "") not in remove]
    if len(filtered) == len(hist):
        return
    path = _analyze_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(filtered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _clear_analyze_history() -> None:
    path = _analyze_history_path()
    if path.is_file():
        path.write_text("[]\n", encoding="utf-8")


@app.route("/api/notifications/feed", methods=["GET"])
def api_notifications_feed():
    if not _is_localhost_request():
        return jsonify({"error": "Local use only."}), 403
    prefs = _read_user_prefs()
    read_ids = prefs.get("notifications_read_ids") or []
    if not isinstance(read_ids, list):
        read_ids = []
    dismissed = _notifications_dismissed_map(prefs)
    return jsonify(
        {
            "analyze_history": _read_analyze_history(),
            "update": check_for_updates(),
            "security": _security_findings_summary(),
            "git": _git_status_summary(),
            "index_stale": _index_stale_summary(),
            "read_ids": read_ids,
            "dismissed": dismissed,
            "current_repo_path": current_repo_path,
        }
    )


@app.route("/api/notifications/read", methods=["POST"])
def api_notifications_read():
    if not _is_localhost_request():
        return jsonify({"error": "Local use only."}), 403
    data = request.get_json() or {}
    ids = data.get("ids")
    mark_all = bool(data.get("all"))
    prefs = _read_user_prefs()
    read_ids = prefs.get("notifications_read_ids") or []
    if not isinstance(read_ids, list):
        read_ids = []
    incoming = []
    if mark_all:
        incoming = [str(x) for x in (data.get("all_ids") or []) if x and str(x) != "live-analyze"]
    elif isinstance(ids, list):
        incoming = [str(x) for x in ids if x and str(x) != "live-analyze"]
    if incoming:
        read_ids = list(dict.fromkeys([*read_ids, *incoming]))
    prefs["notifications_read_ids"] = read_ids[-200:]
    _write_user_prefs(prefs)
    return jsonify({"status": "ok", "read_ids": prefs["notifications_read_ids"]})


@app.route("/api/notifications/dismiss", methods=["POST"])
def api_notifications_dismiss():
    if not _is_localhost_request():
        return jsonify({"error": "Local use only."}), 403
    data = request.get_json() or {}
    dismiss_all = bool(data.get("all"))
    prefs = _read_user_prefs()
    dismissed = _notifications_dismissed_map(prefs)
    history_remove_ids: list[str] = []

    def _apply_dismiss(notif_id: str, state_key: str | None = None) -> None:
        if not notif_id or notif_id == "live-analyze":
            return
        dismissed[str(notif_id)] = state_key if state_key is not None else True
        if str(notif_id).startswith("history-"):
            history_remove_ids.append(str(notif_id)[8:])

    if dismiss_all:
        for entry in data.get("items") or []:
            if isinstance(entry, dict):
                _apply_dismiss(str(entry.get("id") or ""), entry.get("state_key"))
            elif entry:
                _apply_dismiss(str(entry), None)
        if data.get("clear_history"):
            _clear_analyze_history()
            history_remove_ids = []
    elif isinstance(data.get("items"), list):
        for entry in data.get("items"):
            if isinstance(entry, dict):
                _apply_dismiss(str(entry.get("id") or ""), entry.get("state_key"))
    elif isinstance(data.get("ids"), list):
        for notif_id in data.get("ids"):
            _apply_dismiss(str(notif_id), data.get("state_key"))

    if history_remove_ids:
        _remove_analyze_history_entries(history_remove_ids)

    prefs["notifications_dismissed"] = dict(list(dismissed.items())[-300:])
    prefs.pop("notifications_dismissed_ids", None)
    _write_user_prefs(prefs)
    return jsonify({"status": "ok", "dismissed": prefs["notifications_dismissed"]})


# Runs in a subprocess so tkinter is on the main thread of that process (Waitress-safe).
_PICK_FOLDER_PY = (
    "import tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk()\n"
    "r.withdraw()\n"
    "try:\n"
    "    r.attributes('-topmost', True)\n"
    "except tk.TclError:\n"
    "    pass\n"
    "p = filedialog.askdirectory(mustexist=True) or ''\n"
    "print(p, end='')\n"
    "r.destroy()\n"
)


@app.route("/api/pick_folder", methods=["POST"])
def api_pick_folder():
    """Open the OS folder picker on the machine running Flask (this PC). Localhost only.

    PyInstaller builds must use pywebview's JS API pick_folder instead: here
    ``sys.executable`` is LogicLens.exe, which would spawn a second app instance.
    """
    if not _is_localhost_request():
        return jsonify(
            {
                "error": "Folder picker only works when you open LogicLens on this computer "
                "(not over the network).",
            }
        ), 403
    if getattr(sys, "frozen", False):
        return jsonify(
            {
                "error": "Use Open project / File → Open Folder in the app window "
                "(packaged build cannot open the picker from the server).",
            }
        ), 503
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PICK_FOLDER_PY],
            capture_output=True,
            text=True,
            timeout=300,
        )
        path = (proc.stdout or "").strip()
        if proc.returncode != 0:
            err = (proc.stderr or "").strip() or "folder dialog failed"
            return jsonify({"error": err}), 500
        if not path:
            return jsonify({"cancelled": True})
        if not os.path.isdir(path):
            return jsonify({"error": "Invalid folder selected."}), 400
        return jsonify({"path": path})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Folder dialog timed out."}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _read_last_graph_root_file() -> str:
    p = get_data_dir() / "last_graph_project_root.txt"
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@app.route("/api/storage/clear", methods=["POST"])
def api_storage_clear():
    """Clear SQLite graph, manifests, and/or Chroma collections. Localhost only."""
    if not _is_localhost_request():
        return jsonify({"error": "Local use only."}), 403

    data = request.get_json() or {}
    mode = str(data.get("mode") or "all").lower()
    data_dir = get_data_dir()

    if mode == "all":
        try:
            store = SqliteGraphStore(graph_db_path())
            store.clear()
        except Exception as e:
            return jsonify({"error": f"Could not clear graph DB: {e}"}), 500
        for p in data_dir.glob("analysis_manifest_*.json"):
            try:
                p.unlink()
            except OSError:
                pass
        last_root = data_dir / "last_graph_project_root.txt"
        if last_root.is_file():
            try:
                last_root.unlink()
            except OSError:
                pass
        try:
            client = chromadb.PersistentClient(path=str(chroma_dir()))
            for c in client.list_collections():
                try:
                    client.delete_collection(c.name)
                except Exception:
                    pass
        except Exception as e:
            return jsonify({"error": f"Chroma cleanup failed: {e}"}), 500
        return jsonify({"status": "ok", "cleared": "all"})

    if mode == "project":
        raw_path = (data.get("path") or "").strip().strip('"').strip("'")
        if not raw_path or not os.path.isdir(raw_path):
            return jsonify({"error": "Invalid or missing project path."}), 400
        norm_root = normalize_project_file_path(raw_path)
        coll_name = chroma_collection_for_project(raw_path)
        manifest = data_dir / f"analysis_manifest_{coll_name}.json"
        if manifest.is_file():
            try:
                manifest.unlink()
            except OSError:
                pass
        try:
            client = chromadb.PersistentClient(path=str(chroma_dir()))
            try:
                client.delete_collection(coll_name)
            except Exception:
                pass
        except Exception as e:
            return jsonify({"error": f"Chroma cleanup failed: {e}"}), 500

        if _read_last_graph_root_file() == norm_root:
            try:
                store = SqliteGraphStore(graph_db_path())
                store.clear()
            except Exception as e:
                return jsonify({"error": f"Could not clear graph DB: {e}"}), 500
            last_root = data_dir / "last_graph_project_root.txt"
            if last_root.is_file():
                try:
                    last_root.unlink()
                except OSError:
                    pass
        return jsonify({"status": "ok", "cleared": "project", "path": norm_root})

    return jsonify({"error": 'Use mode "all" or "project".'}), 400


def _resolve_analyze_target(data: dict) -> tuple[str, str, dict]:
    """
    Resolve analyze target from local path or GitHub URL/owner+repo.
    Returns (directory_path, display_label, extra_meta).
    """
    github_url = (data.get("github_url") or "").strip()
    path_val = (data.get("path") or "").strip().strip('"').strip("'")

    # Auto-detect GitHub URL in path parameter if github_url is not explicitly set
    if not github_url and (path_val.lower().startswith("https://github.com/") or path_val.lower().startswith("http://github.com/") or path_val.lower().startswith("github.com/")):
        github_url = path_val

    owner = (data.get("github_owner") or "").strip()
    repo = (data.get("github_repo") or "").strip()
    ref = (data.get("github_ref") or data.get("ref") or "").strip()

    if github_url:
        parsed = github_api.parse_github_url(github_url)
        if not parsed:
            raise ValueError("Invalid GitHub URL. Use https://github.com/owner/repo")
        owner, repo = parsed["owner"], parsed["repo"]
        if parsed.get("ref"):
            ref = parsed["ref"]

    if owner and repo:
        token = github_api.load_github_token()
        cache_path = github_api.sync_repo_to_cache(owner, repo, ref or None, token)
        label = f"github:{owner}/{repo}"
        return cache_path, label, {"github_owner": owner, "github_repo": repo, "github_ref": ref or None}

    directory_path = path_val
    if not directory_path:
        raise ValueError("Missing 'path' or GitHub repository details.")
    if not os.path.isdir(directory_path):
        raise ValueError("Invalid directory path. Provide a valid local folder path or GitHub URL.")
    return directory_path, directory_path, {}


def _run_analyze_job(job_id: str, directory_path: str, fast: bool) -> None:
    global current_repo_path
    prev_blame = os.environ.get("LOGICLENS_SKIP_GIT_BLAME")
    if fast:
        os.environ["LOGICLENS_SKIP_GIT_BLAME"] = "1"
    started = time.time()
    try:
        with _analyze_jobs_lock:
            _analyze_jobs[job_id]["status"] = "running"
            _analyze_jobs[job_id]["message"] = "Parsing source files…"
        current_repo_path = directory_path
        result = analyze_project(directory_path)
        duration_ms = int((time.time() - started) * 1000)
        _record_analyze_history(
            directory_path,
            status="success",
            result=result,
            duration_ms=duration_ms,
        )
        with _analyze_jobs_lock:
            _analyze_jobs[job_id]["status"] = "done"
            _analyze_jobs[job_id]["result"] = result
            _analyze_jobs[job_id]["message"] = "Complete"
    except Exception as exc:
        duration_ms = int((time.time() - started) * 1000)
        _record_analyze_history(
            directory_path,
            status="error",
            error=str(exc),
            duration_ms=duration_ms,
        )
        with _analyze_jobs_lock:
            _analyze_jobs[job_id]["status"] = "error"
            _analyze_jobs[job_id]["error"] = str(exc)
            _analyze_jobs[job_id]["message"] = str(exc)
    finally:
        if fast:
            if prev_blame is None:
                os.environ.pop("LOGICLENS_SKIP_GIT_BLAME", None)
            else:
                os.environ["LOGICLENS_SKIP_GIT_BLAME"] = prev_blame


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    global current_repo_path
    data = request.get_json() or {}
    async_mode = bool(data.get("async"))
    fast = bool(data.get("fast"))

    try:
        directory_path, _label, meta = _resolve_analyze_target(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if async_mode:
        job_id = uuid.uuid4().hex
        with _analyze_jobs_lock:
            _analyze_jobs[job_id] = {
                "status": "queued",
                "message": "Queued…",
                "path": directory_path,
                "meta": meta,
            }
        threading.Thread(
            target=_run_analyze_job,
            args=(job_id, directory_path, fast),
            daemon=True,
        ).start()
        return jsonify({"status": "accepted", "job_id": job_id, **meta})

    prev_blame = os.environ.get("LOGICLENS_SKIP_GIT_BLAME")
    if fast:
        os.environ["LOGICLENS_SKIP_GIT_BLAME"] = "1"
    current_repo_path = directory_path
    started = time.time()
    try:
        result = analyze_project(directory_path)
        duration_ms = int((time.time() - started) * 1000)
        _record_analyze_history(
            directory_path,
            status="success",
            result=result,
            duration_ms=duration_ms,
        )
        return jsonify({"status": "success", **result, **meta})
    except Exception as e:
        duration_ms = int((time.time() - started) * 1000)
        _record_analyze_history(
            directory_path,
            status="error",
            error=str(e),
            duration_ms=duration_ms,
        )
        return jsonify({"error": str(e)}), 500
    finally:
        if fast:
            if prev_blame is None:
                os.environ.pop("LOGICLENS_SKIP_GIT_BLAME", None)
            else:
                os.environ["LOGICLENS_SKIP_GIT_BLAME"] = prev_blame


@app.route("/api/analyze/status/<job_id>", methods=["GET"])
def api_analyze_status(job_id: str):
    with _analyze_jobs_lock:
        job = _analyze_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job."}), 404
    payload = {
        "status": job.get("status"),
        "message": job.get("message"),
        "path": job.get("path"),
        "meta": job.get("meta") or {},
    }
    if job.get("status") == "done":
        payload["result"] = job.get("result") or {}
    if job.get("status") == "error":
        payload["error"] = job.get("error")
    return jsonify(payload)


@app.route("/api/github/status", methods=["GET"])
def api_github_status():
    profile = github_api.github_user_profile()
    return jsonify(
        {
            "client_configured": bool(github_api.github_client_id()),
            "logged_in": bool(profile),
            "user": profile,
        }
    )


@app.route("/api/github/device/start", methods=["POST"])
def api_github_device_start():
    try:
        flow = github_api.start_device_flow()
        return jsonify({"status": "ok", **flow})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/github/device/poll", methods=["POST"])
def api_github_device_poll():
    data = request.get_json() or {}
    device_code = (data.get("device_code") or "").strip()
    if not device_code:
        return jsonify({"error": "Missing device_code."}), 400
    result = github_api.poll_device_flow(device_code)
    return jsonify(result)


@app.route("/api/github/logout", methods=["POST"])
def api_github_logout():
    github_api.clear_github_session()
    return jsonify({"status": "ok"})


@app.route("/api/github/repos", methods=["GET"])
def api_github_repos():
    token = github_api.load_github_token()
    if not token:
        return jsonify({"error": "Not signed in to GitHub."}), 401
    try:
        page = max(1, int(request.args.get("page", 1)))
        repos = github_api.GitHubClient(token).list_repos(page=page)
        return jsonify({"status": "ok", "repos": repos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/data_dir", methods=["POST"])
def api_settings_data_dir():
    if not _is_localhost_request():
        return jsonify({"error": "Forbidden."}), 403
    data = request.get_json() or {}
    new_dir = (data.get("path") or "").strip().strip('"').strip("'")
    if not new_dir:
        return jsonify({"error": "Missing path."}), 400
    try:
        p = Path(new_dir)
        p.mkdir(parents=True, exist_ok=True)
        test = p / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
    except OSError as e:
        return jsonify({"error": f"Cannot use that folder: {e}"}), 400

    env_path = get_data_dir() / ".env"
    prev = _parse_env_file(env_path)
    rows = dict(prev)
    rows["LOGICLENS_DATA_DIR"] = str(Path(new_dir).resolve())
    key_order = [
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "CHROMA_PERSIST_PATH",
        "LOGICLENS_TELEMETRY",
        "SENTRY_DSN",
        "GITHUB_CLIENT_ID",
        "LOGICLENS_DATA_DIR",
    ]
    lines = [f"{k}={rows[k]}" for k in key_order if k in rows and rows[k]]
    for k, v in rows.items():
        if k not in key_order and v:
            lines.append(f"{k}={v}")
    lines.append("")
    env_path.write_text("\n".join(lines), encoding="utf-8")
    new_env = Path(new_dir).resolve() / ".env"
    try:
        if new_env.resolve() != env_path.resolve():
            new_env.parent.mkdir(parents=True, exist_ok=True)
            new_env.write_text(env_path.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass
    apply_data_dir_override(new_dir)
    load_app_env()
    return jsonify(
        {
            "status": "ok",
            "data_dir": str(Path(new_dir).resolve()),
            "graph_db": str(graph_db_path()),
            "message": (
                "Storage location updated. New analysis and cache files will use this folder. "
                "Re-run Analyze to rebuild the graph in the new location."
            ),
            "restart_recommended": False,
        }
    )


@app.route("/api/crash-report", methods=["POST"])
def api_crash_report():
    data = request.get_json() or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "Please describe what happened."}), 400

    feedback_dir = get_data_dir() / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    report_id = datetime.now(timezone.utc).strftime("crash_%Y%m%d_%H%M%S") + f"_{uuid.uuid4().hex[:8]}"
    payload = {
        "id": report_id,
        "description": description,
        "app_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "context": data.get("context") or {},
    }
    out = feedback_dir / f"{report_id}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    sent_sentry = False
    if os.environ.get("LOGICLENS_TELEMETRY", "").lower() in ("1", "true", "yes"):
        dsn = (os.environ.get("SENTRY_DSN") or "").strip()
        if dsn:
            try:
                import sentry_sdk

                sentry_sdk.capture_message(
                    f"User crash report ({report_id}): {description[:500]}",
                    level="error",
                )
                sent_sentry = True
            except Exception:
                pass

    return jsonify(
        {
            "status": "ok",
            "report_id": report_id,
            "saved_to": str(out),
            "sent_to_sentry": sent_sentry,
        }
    )


@app.route("/api/graph", methods=["GET"])
def api_graph():
    try:
        store = _graph()
        nodes_dict, edges = store.fetch_full_graph()
        return jsonify({"nodes": list(nodes_dict.values()), "edges": edges})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_dir_tree(path):
    from logiclens.scan_ignore import SKIP_DIR_NAMES, should_skip_dir

    ignores = {
        '.idea',
        '.vscode',
        *SKIP_DIR_NAMES,
    }
    try:
        if not os.path.isdir(path):
            return None

        name = os.path.basename(os.path.abspath(path))
        tree = {"name": name, "type": "directory", "children": []}

        for entry in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name in ignores or should_skip_dir(entry.name):
                continue
            if entry.is_dir():
                sub_tree = get_dir_tree(entry.path)
                if sub_tree:
                    tree["children"].append(sub_tree)
            else:
                tree["children"].append({"name": entry.name, "type": "file"})
        return tree
    except Exception as e:
        print(f"Error scanning directory: {e}")
        return None


@app.route("/api/explorer", methods=["GET"])
def api_explorer():
    path = request.args.get("path", "").strip().strip('"').strip("'")
    if not path:
        return jsonify({"error": "Missing 'path' parameter."}), 400

    tree = get_dir_tree(path)
    if tree is None:
        return jsonify({"error": "Invalid directory path or unable to read."}), 400

    return jsonify({"status": "success", "tree": tree})

def extract_code(filepath, node_name, node_type):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            code = f.read()

        if node_type == "File":
            return code

        ext = os.path.splitext(filepath)[1].lower()
        print(f"[DEBUG] extract_code: path={filepath}, ext={ext}, name={node_name}, type={node_type}")
        if ext not in LANGUAGES or ext not in CONFIGS:
            print(f"[DEBUG] ext {ext} not supported")
            return None

        lang = LANGUAGES[ext]
        conf = CONFIGS[ext]
        parser = Parser(lang)
        tree = parser.parse(bytes(code, "utf8"))

        query_str = conf['cls'] if node_type == "Class" else conf['func']
        query = Query(lang, query_str)
        cursor = tree_sitter.QueryCursor(query)
        matches = cursor.matches(tree.root_node)

        for _pattern_index, match_dict in matches:
            name_node = match_dict.get('function.name') or match_dict.get('class.name')
            def_node  = match_dict.get('function.def') or match_dict.get('class.def')

            if not name_node or not def_node:
                continue

            name_node = name_node[0] if isinstance(name_node, list) else name_node
            def_node = def_node[0] if isinstance(def_node, list) else def_node

            extracted_name = name_node.text.decode('utf8')
            if extracted_name == node_name:
                return def_node.text.decode('utf8')

        print(f"[DEBUG] node_name '{node_name}' not found in file")
    except Exception as e:
        print(f"Error extracting code: {e}")
    return None

@app.route("/api/trace", methods=["GET"])
def api_trace():
    node_id = request.args.get("node_id")
    if not node_id:
        return jsonify({"error": "Missing 'node_id'."}), 400

    try:
        store = _graph()
        incoming, outgoing = store.trace_calls(node_id)
        return jsonify({"status": "success", "incoming": incoming, "outgoing": outgoing})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/source", methods=["GET"])
def api_source():
    path = request.args.get("file")
    node_name = request.args.get("name")
    node_type = request.args.get("type", "Function")

    if not path or not node_name:
        return jsonify({"error": "Missing 'file' or 'name' parameter."}), 400

    code_segment = extract_code(path, node_name, node_type)
    if not code_segment:
        return jsonify({"error": f"{node_type} source not found in the file."}), 404

    return jsonify({"status": "success", "code": code_segment})

def generate_ai_explanation(code_segment: str, node_type: str) -> tuple[str, bool]:
    prov = active_ai_provider()
    prompt = (
        f"Explain exactly what this {node_type} does. Be concise: at most 2 sentences. "
        f"Plain English for a developer overview. Do not include scores, ratings, or confidence labels.\n\n"
        f"```\n{code_segment[:8000]}\n```"
    )
    
    import urllib.request
    import urllib.error

    if prov == "gemini":
        key = gemini_api_key()
        if not key:
            if groq_api_key():
                prov = "groq"
            else:
                return ("Gemini API Key is missing. Open Setup (/setup) or AI Settings to set your GEMINI_API_KEY.", False)
        if prov == "gemini":
            model = gemini_model()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 150}
            }
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])
                text = parts[0].get("text", "").strip() if parts else "No explanation generated."
                return (text, True)

    if prov == "groq":
        key = groq_api_key()
        if not key:
            if gemini_api_key():
                # fallback to gemini
                model = gemini_model()
                gkey = gemini_api_key()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gkey}"
                headers = {"Content-Type": "application/json"}
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 150}
                }
                req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])
                    text = parts[0].get("text", "").strip() if parts else "No explanation generated."
                    return (text, True)
            return ("Groq API Key is missing. Open Setup (/setup) or AI Settings to set your GROQ_API_KEY.", False)
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "LogicLens-Desktop"
        }
        payload = {
            "model": "qwen/qwen3.8-27b",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
            "temperature": 0.2
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"].strip()
            return (text, True)

    if prov == "ollama":
        host = ollama_host()
        model = ollama_model()
        url = f"{host}/api/generate"
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data.get("response", "").strip()
            return (text, True)

    return ("No AI provider configured.", False)


@app.route("/api/explain", methods=["GET"])
def api_explain():
    path = request.args.get("file")
    node_name = request.args.get("name")
    node_type = request.args.get("type", "Function")

    if not path or not node_name:
        return jsonify({"error": "Missing 'file' or 'name' parameter."}), 400

    code_segment = extract_code(path, node_name, node_type)
    if not code_segment:
        return jsonify({"error": f"{node_type} source not found in the file."}), 404

    try:
        explanation, ok = generate_ai_explanation(code_segment, node_type)
        return jsonify({"status": "success", "explanation": explanation, "configured": ok})
    except Exception as e:
        return jsonify({"error": f"AI Generation Failed: {e}"}), 500


@app.route("/api/check_env", methods=["GET"])
def api_check_env():
    prov = active_ai_provider()
    g_key = gemini_api_key()
    q_key = groq_api_key()
    is_conf = (
        (prov == "gemini" and bool(g_key))
        or (prov == "groq" and bool(q_key))
        or (prov == "ollama")
    )
    return jsonify({
        "provider": prov,
        "is_configured": is_conf,
        "groq_api_key_present": bool(q_key),
        "gemini_api_key_present": bool(g_key),
        "ollama_model": ollama_model(),
        "ollama_host": ollama_host(),
    })

@app.route("/api/functions", methods=["GET"])
def api_functions():
    try:
        store = _graph()
        names = store.list_function_names()
        return jsonify({"functions": names})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/whatif", methods=["POST"])
def api_whatif():
    data = request.get_json()
    if not data or "function" not in data:
        return jsonify({"error": "Missing 'function' in request body."}), 400

    target = data["function"].strip()
    global current_repo_path
    proj = current_repo_path or ""

    @stream_with_context
    def stream():
        old = os.environ.get("LOGICLENS_ACTIVE_PROJECT")
        try:
            if proj:
                os.environ["LOGICLENS_ACTIVE_PROJECT"] = proj
            else:
                os.environ.pop("LOGICLENS_ACTIVE_PROJECT", None)
            from whatif_engine import run_whatif_engine

            yield from run_whatif_engine(target)
        except Exception as e:
            import traceback

            err_tb = traceback.format_exc()
            msg = str(e) or type(e).__name__
            payload = json.dumps(
                {
                    "role": "system",
                    "content": msg,
                    "type": "error",
                    "text": f"{msg}\n\n{err_tb}",
                }
            )
            yield f"data: {payload}\n\n"
        finally:
            if old is None:
                os.environ.pop("LOGICLENS_ACTIVE_PROJECT", None)
            else:
                os.environ["LOGICLENS_ACTIVE_PROJECT"] = old

    # Do not set "Connection" — hop-by-hop headers are forbidden for WSGI apps (PEP 3333);
    # Waitress raises AssertionError and returns 500 if they are present.
    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

@app.route("/api/git/summary", methods=["GET"])
def api_git_summary():
    global current_repo_path
    if not current_repo_path:
        return jsonify({"error": "Please analyze a project folder first."}), 400

    # 1. Try local Git repository first
    try:
        repo = git.Repo(current_repo_path, search_parent_directories=True)
        commits = []
        for c in repo.iter_commits('HEAD', max_count=15):
            commits.append({
                "hash": c.hexsha[:7],
                "message": c.summary,
                "author": c.author.name,
                "date": c.authored_datetime.strftime("%b %d, %Y")
            })

        churn = {}
        try:
            for c in repo.iter_commits('HEAD', max_count=50):
                for file in c.stats.files.keys():
                    if file.endswith(('.py', '.js', '.ts', '.java', '.go', '.cpp', '.html', '.css', '.rs', '.kt', '.swift')):
                        churn[file] = churn.get(file, 0) + 1
        except Exception:
            pass

        top_churn = [{"file": k, "changes": v} for k, v in sorted(churn.items(), key=lambda x: x[1], reverse=True)[:10]]

        return jsonify({
            "status": "success",
            "commits": commits,
            "churn": top_churn
        })
    except Exception:
        pass

    # 2. Try GitHub synced repository metadata (.github_source.json)
    meta_file = os.path.join(current_repo_path, ".github_source.json")
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            owner = meta.get("owner")
            repo_name = meta.get("repo")
            if owner and repo_name:
                token = github_api.load_github_token()
                url = f"https://api.github.com/repos/{owner}/{repo_name}/commits?per_page=50"
                headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "LogicLens-Desktop"}
                if token:
                    headers["Authorization"] = f"token {token}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                commits = []
                churn = {}
                for item in data:
                    c_hash = item.get("sha", "")[:7]
                    commit_info = item.get("commit", {})
                    msg = (commit_info.get("message") or "").split("\n")[0]
                    author = commit_info.get("author", {}).get("name") or item.get("author", {}).get("login") or "Unknown"
                    date_raw = commit_info.get("author", {}).get("date") or ""
                    date_str = date_raw[:10]
                    if date_raw:
                        try:
                            import datetime
                            dt = datetime.datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
                            date_str = dt.strftime("%b %d, %Y")
                        except Exception:
                            pass
                    if len(commits) < 15:
                        commits.append({
                            "hash": c_hash,
                            "message": msg,
                            "author": author,
                            "date": date_str
                        })

                top_churn = [{"file": k, "changes": v} for k, v in sorted(churn.items(), key=lambda x: x[1], reverse=True)[:10]]
                return jsonify({
                    "status": "success",
                    "commits": commits,
                    "churn": top_churn
                })
        except Exception as exc:
            print("[GitHub Commit Fetch Error]:", exc)

    return jsonify({"error": "The analyzed folder is not a Git repository."}), 400

@app.route("/api/search", methods=["GET"])
def api_search():
    q = request.args.get("q")
    if not q:
        return jsonify({"error": "Missing 'q' parameter."}), 400

    global current_repo_path
    if not current_repo_path:
        return jsonify(
            {"error": "Open and analyze a project first so semantic search uses the right index."}
        ), 400

    try:
        chroma_client = chromadb.PersistentClient(path=str(chroma_dir()))
        coll = chroma_collection_for_project(current_repo_path)
        try:
            collection = chroma_client.get_collection(name=coll)
        except Exception:
            return jsonify({"error": "ChromaDB collection not found. Please analyze a project first."}), 404

        results = collection.query(query_texts=[q], n_results=5)

        matches = []
        if results and 'ids' in results and results['ids'] and len(results['ids'][0]) > 0:
            for i in range(len(results['ids'][0])):
                dist = results['distances'][0][i] if 'distances' in results and results['distances'] else 0
                similarity = max(0.0, min(1.0, 1.0 - dist))
                matches.append({
                    "id": results['ids'][0][i],
                    "code_snippet": results['documents'][0][i],
                    "metadata": results['metadatas'][0][i],
                    "similarity": similarity
                })

        return jsonify({"status": "success", "results": matches})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def create_app():
    return app


if __name__ == "__main__":
    if not use_debug_server():
        print(
            "LogicLens is meant to run as a desktop app:\n"
            "  python desktop_main.py\n\n"
            "To use a browser against localhost (development only), set:\n"
            "  LOGICLENS_DEBUG=1\n"
            "then run python app.py again.",
            file=sys.stderr,
        )
        sys.exit(1)
    app.run(debug=True, host=flask_host(), port=flask_port())
