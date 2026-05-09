from logiclens.config import (
    chroma_collection_for_project,
    chroma_dir,
    flask_host,
    flask_port,
    get_data_dir,
    graph_db_path,
    load_app_env,
    normalize_project_file_path,
    use_debug_server,
)

load_app_env()

import json
import os
import subprocess
import sys
import git

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response,
    send_from_directory,
    stream_with_context,
)
from extractor import analyze_project, LANGUAGES, CONFIGS
import tree_sitter
from tree_sitter import Parser, Query
import chromadb

from pathlib import Path

from logiclens.sqlite_graph import SqliteGraphStore
from logiclens.telemetry import init_flask_telemetry
from logiclens.updates import check_for_updates
from logiclens.version import __version__

app = Flask(__name__, static_folder="static", static_url_path="/static")
init_flask_telemetry(app)

current_repo_path = None


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
    return render_template("setup.html", data_dir=str(get_data_dir()))


@app.route("/onboarding", methods=["GET"])
def onboarding_page():
    return render_template("onboarding.html", data_dir=str(get_data_dir()))


@app.route("/setup", methods=["POST"])
def setup_save():
    data = request.get_json() or {}
    groq = (data.get("groq_api_key") or "").strip()
    gemini = (data.get("gemini_api_key") or "").strip()
    if not groq:
        return jsonify({"error": "GROQ_API_KEY is required for AI features."}), 400
    data_dir = get_data_dir()
    env_path = data_dir / ".env"
    prev = _parse_env_file(env_path)
    telemetry_on = bool(data.get("telemetry_opt_in"))
    rows: dict[str, str] = {
        "GROQ_API_KEY": groq,
        "GEMINI_API_KEY": gemini,
        "CHROMA_PERSIST_PATH": str(chroma_dir().resolve()),
        "LOGICLENS_TELEMETRY": "1" if telemetry_on else "0",
    }
    sentry_dsn = (data.get("sentry_dsn") or "").strip()
    if sentry_dsn:
        rows["SENTRY_DSN"] = sentry_dsn
    elif prev.get("SENTRY_DSN"):
        rows["SENTRY_DSN"] = prev["SENTRY_DSN"]

    key_order = [
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "CHROMA_PERSIST_PATH",
        "LOGICLENS_TELEMETRY",
        "SENTRY_DSN",
    ]
    lines = [f"{k}={rows[k]}" for k in key_order if k in rows] + [""]
    env_path.write_text("\n".join(lines), encoding="utf-8")
    load_app_env()
    return jsonify(
        {
            "status": "success",
            "message": "Saved. Restart the app for telemetry changes to fully apply.",
        }
    )


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
    data_dir = get_data_dir()
    env_file = data_dir / ".env"
    return jsonify(
        {
            "data_dir": str(data_dir),
            "graph_db": str(graph_db_path()),
            "user_env_present": env_file.is_file(),
            "groq_configured": bool(os.environ.get("GROQ_API_KEY")),
            "app_version": __version__,
            "telemetry_enabled": os.environ.get("LOGICLENS_TELEMETRY", "").lower()
            in ("1", "true", "yes")
            and bool((os.environ.get("SENTRY_DSN") or "").strip()),
            "indexed_projects": _indexed_projects_from_manifests(),
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


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    global current_repo_path
    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({"error": "Missing 'path' in request body."}), 400

    directory_path = data["path"].strip().strip('"').strip("'")
    if not os.path.isdir(directory_path):
        return jsonify({"error": "Invalid directory path."}), 400

    current_repo_path = directory_path

    try:
        result = analyze_project(directory_path)
        return jsonify({"status": "success", **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/graph", methods=["GET"])
def api_graph():
    try:
        store = _graph()
        nodes_dict, edges = store.fetch_full_graph()
        return jsonify({"nodes": list(nodes_dict.values()), "edges": edges})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def get_dir_tree(path):
    from logiclens.scan_ignore import SKIP_DIR_NAMES

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
            if entry.name in ignores or entry.name.startswith('.'):
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

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return jsonify({
            "status": "success",
            "explanation": "Groq API Key is missing. Open Setup (/setup) or set GROQ_API_KEY in your environment to enable AI explanations."
        })

    try:
        import urllib.request
        import urllib.error
        import json

        prompt = (
            f"Explain exactly what this {node_type} does. Be concise: at most 2 sentences. "
            f"Plain English for a developer overview. Do not include scores, ratings, or confidence labels.\n\n"
            f"```python\n{code_segment[:8000]}\n```"
        )

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100,
            "temperature": 0.2
        }

        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method="POST")
        with urllib.request.urlopen(req) as response:
            res_body = response.read()
            text = json.loads(res_body)["choices"][0]["message"]["content"].strip()

        return jsonify({"status": "success", "explanation": text})
    except Exception as e:
        return jsonify({"error": f"AI Generation Failed: {e}"}), 500

@app.route("/api/check_env", methods=["GET"])
def api_check_env():
    has_key = bool(os.environ.get("GROQ_API_KEY"))
    return jsonify({"groq_api_key_present": has_key})

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

    try:
        repo = git.Repo(current_repo_path, search_parent_directories=True)
    except git.exc.InvalidGitRepositoryError:
        return jsonify({"error": "The analyzed folder is not a Git repository."}), 400
    except Exception as e:
        return jsonify({"error": f"Git Error: {str(e)}"}), 500

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
                if file.endswith(('.py', '.js', '.ts', '.java', '.go', '.cpp')):
                    churn[file] = churn.get(file, 0) + 1
    except Exception:
        pass

    top_churn = [{"file": k, "changes": v} for k, v in sorted(churn.items(), key=lambda x: x[1], reverse=True)[:10]]

    return jsonify({
        "status": "success",
        "commits": commits,
        "churn": top_churn
    })

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
