from logiclens.config import (
    chroma_collection_for_project,
    chroma_dir,
    flask_host,
    flask_port,
    get_data_dir,
    graph_db_path,
    load_app_env,
    use_debug_server,
)

load_app_env()

import os
import subprocess
import sys
import git

from flask import Flask, request, jsonify, render_template, Response, send_from_directory
from extractor import analyze_project, LANGUAGES, CONFIGS
import tree_sitter
from tree_sitter import Parser, Query
import chromadb

from logiclens.sqlite_graph import SqliteGraphStore

app = Flask(__name__, static_folder="static", static_url_path="/static")

current_repo_path = None


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


@app.route("/setup", methods=["POST"])
def setup_save():
    data = request.get_json() or {}
    groq = (data.get("groq_api_key") or "").strip()
    gemini = (data.get("gemini_api_key") or "").strip()
    if not groq:
        return jsonify({"error": "GROQ_API_KEY is required for AI features."}), 400
    data_dir = get_data_dir()
    env_path = data_dir / ".env"
    lines = [
        f"GROQ_API_KEY={groq}",
        f"GEMINI_API_KEY={gemini}",
        f"CHROMA_PERSIST_PATH={chroma_dir().resolve()}",
        "",
    ]
    env_path.write_text("\n".join(lines), encoding="utf-8")
    load_app_env()
    return jsonify({"status": "success", "message": "Saved. Restart the app if it was already running."})


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
        }
    )


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
    """Open the OS folder picker on the machine running Flask (this PC). Localhost only."""
    if not _is_localhost_request():
        return jsonify(
            {
                "error": "Folder picker only works when you open LogicLens on this computer "
                "(not over the network).",
            }
        ), 403
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

    def stream():
        old = os.environ.get("LOGICLENS_ACTIVE_PROJECT")
        try:
            if proj:
                os.environ["LOGICLENS_ACTIVE_PROJECT"] = proj
            else:
                os.environ.pop("LOGICLENS_ACTIVE_PROJECT", None)
            from whatif_engine import run_whatif_engine

            yield from run_whatif_engine(target)
        finally:
            if old is None:
                os.environ.pop("LOGICLENS_ACTIVE_PROJECT", None)
            else:
                os.environ["LOGICLENS_ACTIVE_PROJECT"] = old

    return Response(
        stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
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
