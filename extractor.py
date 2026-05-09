import hashlib
import json
import os
import re
from pathlib import Path

import chromadb
import git
import tree_sitter
import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Query

from logiclens.config import (
    chroma_collection_for_project,
    chroma_dir,
    get_data_dir,
    graph_db_path,
    load_app_env,
    normalize_project_file_path,
)
from logiclens.scan_ignore import prune_walk_dirs, should_skip_parsed_file

load_app_env()
from logiclens.sqlite_graph import SqliteGraphStore, apply_entities_to_store

# Load languages
LANGUAGES = {}

def load_lang(ext, module_name, lang_attr="language"):
    try:
        module = __import__(module_name)
        func = getattr(module, lang_attr)
        LANGUAGES[ext] = Language(func())
    except Exception as e:
        print(f"Warning: Could not load tree-sitter parser for {ext}: {e}")

load_lang('.py', 'tree_sitter_python')
load_lang('.js', 'tree_sitter_javascript')
load_lang('.jsx', 'tree_sitter_javascript')
load_lang('.ts', 'tree_sitter_typescript', 'language_typescript')
load_lang('.tsx', 'tree_sitter_typescript', 'language_typescript')
load_lang('.java', 'tree_sitter_java')
load_lang('.go', 'tree_sitter_go')
load_lang('.cpp', 'tree_sitter_cpp')
load_lang('.cc', 'tree_sitter_cpp')
load_lang('.h', 'tree_sitter_cpp')
load_lang('.hpp', 'tree_sitter_cpp')

# AST Query Configurations
CONFIGS = {
    ".py": {
        "func": "(function_definition name: (identifier) @function.name) @function.def",
        "cls": "(class_definition name: (identifier) @class.name) @class.def",
        "call": "(call function: (identifier) @callee)"
    },
    ".js": {
        "func": "(function_declaration name: (identifier) @function.name) @function.def\n(method_definition name: (property_identifier) @function.name) @function.def\n(variable_declarator name: (identifier) @function.name value: (arrow_function)) @function.def",
        "cls": "(class_declaration name: (identifier) @class.name) @class.def",
        "call": "(call_expression function: (identifier) @callee)"
    },
    ".ts": {
        "func": "(function_declaration name: (identifier) @function.name) @function.def\n(method_definition name: (property_identifier) @function.name) @function.def\n(variable_declarator name: (identifier) @function.name value: (arrow_function)) @function.def",
        "cls": "(class_declaration name: (type_identifier) @class.name) @class.def",
        "call": "(call_expression function: (identifier) @callee)"
    },
    ".java": {
        "func": "(method_declaration name: (identifier) @function.name) @function.def",
        "cls": "(class_declaration name: (identifier) @class.name) @class.def",
        "call": "(method_invocation name: (identifier) @callee)"
    },
    ".go": {
        "func": "(function_declaration name: (identifier) @function.name) @function.def\n(method_declaration name: (field_identifier) @function.name) @function.def",
        "cls": "(type_spec name: (type_identifier) @class.name type: (struct_type)) @class.def",
        "call": "(call_expression function: (identifier) @callee)"
    },
    ".cpp": {
        "func": "(function_definition declarator: (function_declarator declarator: (identifier) @function.name)) @function.def",
        "cls": "(class_specifier name: (type_identifier) @class.name) @class.def",
        "call": "(call_expression function: (identifier) @callee)"
    }
}
CONFIGS[".jsx"] = CONFIGS[".js"]
CONFIGS[".tsx"] = CONFIGS[".ts"]
CONFIGS[".cc"] = CONFIGS[".cpp"]
CONFIGS[".h"] = CONFIGS[".cpp"]
CONFIGS[".hpp"] = CONFIGS[".cpp"]


def _graph_store() -> SqliteGraphStore:
    return SqliteGraphStore(graph_db_path())


ANALYSIS_MANIFEST_VERSION = 2


def _chroma_entity_id(norm_path: str, kind: str, name: str) -> str:
    """Stable Chroma id per file + kind + symbol (avoids basename collisions)."""
    payload = f"{norm_path}\0{kind}\0{name}".encode("utf-8")
    return "e" + hashlib.sha256(payload).hexdigest()[:31]


def _file_fingerprint(path: str) -> str:
    """Fast change detection: size + mtime (nanoseconds)."""
    st = os.stat(path)
    return f"{st.st_mtime_ns}:{st.st_size}"


def _manifest_path_for_project(project_key: str) -> Path:
    return get_data_dir() / f"analysis_manifest_{project_key}.json"


def _last_graph_root_path() -> Path:
    return get_data_dir() / "last_graph_project_root.txt"


def _read_last_graph_root() -> str:
    p = _last_graph_root_path()
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_last_graph_root(norm_root: str) -> None:
    p = _last_graph_root_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(norm_root, encoding="utf-8")


def _load_analysis_manifest(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_analysis_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _get_or_create_chroma_collection(chroma_client: chromadb.PersistentClient, coll_name: str):
    try:
        return chroma_client.get_collection(coll_name)
    except Exception:
        return chroma_client.create_collection(
            name=coll_name,
            metadata={"hnsw:space": "cosine"},
        )


def _chroma_delete_vectors_for_file(collection, norm_path: str) -> None:
    try:
        res = collection.get(where={"filepath": norm_path}, include=[])
        ids = res.get("ids") or []
        if ids:
            collection.delete(ids=ids)
    except Exception as exc:
        print(f"  [Chroma] Could not purge vectors for {norm_path}: {exc}")


def get_author(file_path, line_number):
    try:
        repo = git.Repo(os.path.dirname(file_path), search_parent_directories=True)
        rel_path = os.path.relpath(file_path, repo.working_dir)
        # Use rel_path for blame, replacing backslashes on Windows
        blame = repo.blame('HEAD', rel_path.replace("\\", "/"))
        current_line = 0
        for commit, lines in blame:
            for _ in lines:
                current_line += 1
                if current_line == line_number:
                    return commit.author.name
    except Exception:
        pass  # Never leak raw exception text into queries
    return "Unknown"


def scan_vulnerabilities(raw_code):
    vulns = []
    if re.search(r'AKIA[0-9A-Z]{16}', raw_code):
        vulns.append("Hardcoded AWS Access Key")
    if re.search(r'ghp_[a-zA-Z0-9]{36}', raw_code):
        vulns.append("Hardcoded GitHub Token")
    if re.search(r'(?i)password\s*=\s*[\'"][^\'"]+[\'"]', raw_code):
        vulns.append("Hardcoded Password")
    if re.search(r'(?i)api_?key\s*=\s*[\'"][^\'"]+[\'"]', raw_code):
        vulns.append("Hardcoded API Key")
    if re.search(r'(?i)SELECT.*FROM.*\+.*', raw_code) or re.search(r'(?i)f[\'"]SELECT.*\{.*\}.*[\'"]', raw_code):
        vulns.append("Potential SQL Injection Risk")
    return vulns

def extract_entities_from_file(file_path, chroma_collection):
    """Parse a single file and return lists of class and function dicts,
    upserting each entity into ChromaDB. Also returns basic file metadata."""

    norm_meta = normalize_project_file_path(file_path)

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in LANGUAGES or ext not in CONFIGS:
        return {'functions': [], 'classes': [], 'file_path': file_path}
        
    lang = LANGUAGES[ext]
    conf = CONFIGS[ext]
    
    parser = Parser(lang)

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            code = f.read()
    except Exception as e:
        print(f"  [Skip] Cannot read {file_path}: {e}")
        return {'functions': [], 'classes': [], 'file_path': file_path}

    tree = parser.parse(bytes(code, "utf8"))

    try:
        query = Query(lang, conf['func'])
        cursor = tree_sitter.QueryCursor(query)
        matches = cursor.matches(tree.root_node)
    except Exception as e:
        print(f"  [Error] Func query failed for {ext}: {e}")
        matches = []

    try:
        class_query = Query(lang, conf['cls'])
        class_cursor = tree_sitter.QueryCursor(class_query)
        class_matches = class_cursor.matches(tree.root_node)
    except Exception as e:
        print(f"  [Error] Class query failed for {ext}: {e}")
        class_matches = []

    classes = []
    class_scopes = []  # To map functions to classes

    for _pattern_index, match_dict in class_matches:
        name_node = match_dict.get('class.name')
        def_node  = match_dict.get('class.def')
        if not name_node or not def_node:
            continue
        
        name_node = name_node[0] if isinstance(name_node, list) else name_node
        def_node = def_node[0] if isinstance(def_node, list) else def_node
        
        name = name_node.text.decode('utf8')
        start_line = name_node.start_point[0] + 1
        raw_code = def_node.text.decode('utf8')
        author = get_author(file_path, start_line)
        vulns = scan_vulnerabilities(raw_code)
        
        chroma_id = _chroma_entity_id(norm_meta, "class", name)
        try:
            chroma_collection.upsert(
                documents=[raw_code],
                metadatas=[{
                    "filepath": norm_meta,
                    "start_line": start_line,
                    "author": author,
                    "name": name,
                    "type": "class",
                }],
                ids=[chroma_id],
            )
        except Exception:
            pass
            
        cls_data = {
            'name': name,
            'line': start_line,
            'author': author,
            'vulnerabilities': vulns,
            'start_byte': def_node.start_byte,
            'end_byte': def_node.end_byte
        }
        classes.append(cls_data)
        class_scopes.append(cls_data)

    functions = []
    for _pattern_index, match_dict in matches:
        name_node = match_dict.get('function.name')
        def_node  = match_dict.get('function.def')
        if not name_node or not def_node:
            continue

        name_node = name_node[0] if isinstance(name_node, list) else name_node
        def_node = def_node[0] if isinstance(def_node, list) else def_node

        name       = name_node.text.decode('utf8')
        start_line = name_node.start_point[0] + 1
        raw_code   = def_node.text.decode('utf8')
        vulns = scan_vulnerabilities(raw_code)

        # Find calls inside this function's body
        calls = []
        try:
            call_query = Query(lang, conf['call'])
            call_cursor = tree_sitter.QueryCursor(call_query)
            call_captures = call_cursor.captures(def_node)
            if 'callee' in call_captures:
                for callee_node in call_captures['callee']:
                    calls.append(callee_node.text.decode('utf8'))
        except Exception:
            pass

        author = get_author(file_path, start_line)

        # Disconnected Islands Regex Scan
        api_exposures = []
        api_calls = []
        
        # Backend exposures (Python Flask / Java Spring)
        # e.g., @app.route('/api/users'), @GetMapping("/api/users")
        exposures_matches = re.findall(r'@(?:app\.route|(?:Get|Post|Put|Delete|Patch)Mapping)\([\'"]([^\'"]+)[\'"]', raw_code)
        api_exposures.extend(exposures_matches)
        
        # Frontend calls (fetch, axios)
        # e.g., fetch('/api/users'), axios.get('/api/users')
        calls_matches = re.findall(r'(?:fetch|axios\.(?:get|post|put|delete|patch))\([\'"]([^\'"]+)[\'"]', raw_code)
        api_calls.extend(calls_matches)

        # Check if function belongs to a class
        parent_class = None
        for cls in class_scopes:
            if cls['start_byte'] <= def_node.start_byte and cls['end_byte'] >= def_node.end_byte:
                if not parent_class or (cls['end_byte'] - cls['start_byte'] < parent_class['end_byte'] - parent_class['start_byte']):
                    parent_class = cls
        
        parent_class_name = parent_class['name'] if parent_class else None

        chroma_id = _chroma_entity_id(norm_meta, "function", name)
        try:
            chroma_collection.upsert(
                documents=[raw_code],
                metadatas=[{
                    "filepath": norm_meta,
                    "start_line": start_line,
                    "author": author,
                    "name": name,
                    "type": "function",
                }],
                ids=[chroma_id],
            )
        except Exception as e:
            pass

        functions.append({
            'name':   name,
            'line':   start_line,
            'calls':  list(set(calls)),
            'author': author,
            'vulnerabilities': vulns,
            'parent_class': parent_class_name,
            'api_exposures': list(set(api_exposures)),
            'api_calls': list(set(api_calls))
        })

    return {
        'file_path': file_path,
        'classes': classes,
        'functions': functions
    }



def analyze_project(directory_path):
    """
    Index a project into SQLite + Chroma.

    By default uses **incremental** updates when re-analyzing the same root: only
    added/changed/removed files are processed (fingerprint = mtime_ns + size).

    Set ``LOGICLENS_FULL_ANALYZE=1`` to force a full graph + vector rebuild.
    """
    print(f"\n=== Analyzing project: {directory_path} ===\n")

    norm_root = normalize_project_file_path(directory_path)
    if not os.path.isdir(directory_path):
        print(f"Invalid directory: {directory_path}")
        return {"files": 0, "functions": 0, "error": "invalid_directory"}

    force_full = os.environ.get("LOGICLENS_FULL_ANALYZE", "").lower() in (
        "1",
        "true",
        "yes",
    )
    project_key = chroma_collection_for_project(directory_path)
    manifest_path = _manifest_path_for_project(project_key)
    manifest = _load_analysis_manifest(manifest_path)

    manifest_ok = (
        bool(manifest)
        and manifest.get("version") == ANALYSIS_MANIFEST_VERSION
        and manifest.get("root") == norm_root
    )

    prev_graph_root = _read_last_graph_root()
    project_switched = bool(prev_graph_root) and prev_graph_root != norm_root

    store = _graph_store()
    chroma_path = str(chroma_dir())
    chroma_client = chromadb.PersistentClient(path=chroma_path)
    coll_name = project_key

    target_files = []
    for root, dirs, files in os.walk(directory_path):
        prune_walk_dirs(dirs)
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in LANGUAGES:
                continue
            fp = os.path.join(root, fname)
            if should_skip_parsed_file(fp):
                continue
            target_files.append(fp)

    if not target_files:
        print(f"No supported code files found in {directory_path}")
        if manifest_ok:
            _save_analysis_manifest(
                manifest_path,
                {"version": ANALYSIS_MANIFEST_VERSION, "root": norm_root, "files": {}},
            )
            _write_last_graph_root(norm_root)
        return {"files": 0, "functions": 0}

    file_keys = [normalize_project_file_path(fp) for fp in target_files]
    key_to_fp = dict(zip(file_keys, target_files))

    do_full = force_full or not manifest_ok or project_switched
    if project_switched and not force_full:
        print(
            f"[Analyze] Project root changed (graph was for another folder) — full rebuild.\n"
        )
    collection = None

    if not do_full:
        collection = _get_or_create_chroma_collection(chroma_client, coll_name)
        try:
            ch_cnt = int(collection.count())
        except Exception:
            ch_cnt = 0
        old_files = manifest.get("files") or {}
        if ch_cnt == 0 and old_files:
            print(
                "[Analyze] Chroma collection empty but manifest present — full re-index."
            )
            do_full = True

    if do_full:
        print("[Graph] Full rebuild: clearing SQLite graph...")
        try:
            store.clear()
            print("[Graph] Graph cleared.")
        except Exception as e:
            print(f"[Graph] Error clearing graph: {e}")
            raise

        print(f"[Chroma] Resetting '{coll_name}' in {chroma_path}...")
        try:
            chroma_client.delete_collection(coll_name)
            print("[Chroma] Deleted existing collection.")
        except Exception:
            print("[Chroma] No existing collection — starting fresh.")
        collection = chroma_client.create_collection(
            name=coll_name,
            metadata={"hnsw:space": "cosine"},
        )
        print(f"[Chroma] Created fresh '{coll_name}' collection.")

        files_to_process = list(target_files)
        keys_removed = []
        incremental = False
    else:
        old_files = manifest.get("files") or {}
        fingerprints = {nk: _file_fingerprint(key_to_fp[nk]) for nk in file_keys}
        keys_removed = [k for k in old_files if k not in key_to_fp]
        files_to_process = [
            key_to_fp[nk]
            for nk in file_keys
            if fingerprints[nk] != old_files.get(nk)
        ]
        incremental = True
        skipped = len(target_files) - len(files_to_process)
        print(
            f"[Analyze] Incremental: {len(files_to_process)} file(s) to update, "
            f"{skipped} unchanged, {len(keys_removed)} removed (tracked).\n"
        )

    total_functions = 0
    updated_manifest = {}

    if incremental:
        for nk in keys_removed:
            print(f"  Removing (deleted): {nk}")
            _chroma_delete_vectors_for_file(collection, nk)
            store.delete_file_subgraph(nk)
        store.prune_orphan_api_routes()

    for fp in files_to_process:
        nk = normalize_project_file_path(fp)
        if incremental:
            print(f"  Updating: {fp}")
            _chroma_delete_vectors_for_file(collection, nk)
            store.delete_file_subgraph(nk)
        else:
            print(f"  Parsing: {fp}")

        entities = extract_entities_from_file(fp, collection)
        funcs_len = len(entities["functions"])
        cls_len = len(entities["classes"])
        print(f"    -> {funcs_len} function(s), {cls_len} class(es) found")
        total_functions += funcs_len
        apply_entities_to_store(store, entities, fp)

    if incremental:
        store.prune_orphan_api_routes()

    for nk in file_keys:
        updated_manifest[nk] = _file_fingerprint(key_to_fp[nk])

    _save_analysis_manifest(
        manifest_path,
        {
            "version": ANALYSIS_MANIFEST_VERSION,
            "root": norm_root,
            "files": updated_manifest,
        },
    )
    _write_last_graph_root(norm_root)

    print(f"\n[Graph] SQLite write complete ({graph_db_path()}).")

    graph_fn_count = len(store.list_function_names())
    mode = "incremental" if incremental else "full"
    print(
        f"\n=== Complete: {len(target_files)} file(s), "
        f"{graph_fn_count} function node(s) in graph, "
        f"{total_functions} parsed this run ({mode}) ===\n"
    )

    result = {
        "files": len(target_files),
        "functions": graph_fn_count,
        "functions_parsed_this_run": total_functions,
        "incremental": incremental,
    }
    if incremental:
        result["updated_files"] = len(files_to_process)
        result["skipped_files"] = len(target_files) - len(files_to_process)
        result["removed_files"] = len(keys_removed)
    return result


# -- Allow direct CLI usage -------------------------------------------------
if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    analyze_project(target)
