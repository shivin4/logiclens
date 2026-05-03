import os
import git
import tree_sitter
from tree_sitter import Language, Parser, Query
import tree_sitter_python as tspython
from neo4j import GraphDatabase
import chromadb

# Neo4j connection details
URI = "neo4j://127.0.0.1:7687"
AUTH = ("neo4j", "password123")

# Initialize Tree-sitter for Python
PY_LANGUAGE = Language(tspython.language())
parser = Parser(PY_LANGUAGE)


def get_author(file_path, line_number):
    try:
        repo = git.Repo(".", search_parent_directories=True)
        blame = repo.blame('HEAD', file_path)
        current_line = 0
        for commit, lines in blame:
            for _ in lines:
                current_line += 1
                if current_line == line_number:
                    return commit.author.name
    except Exception:
        pass  # Never leak raw exception text into queries
    return "Unknown"


def extract_entities_from_file(file_path, chroma_collection):
    """Parse a single Python file and return lists of class and function dicts,
    upserting each entity into ChromaDB. Also returns basic file metadata."""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            code = f.read()
    except Exception as e:
        print(f"  [Skip] Cannot read {file_path}: {e}")
        return []

    tree = parser.parse(bytes(code, "utf8"))

    # Query to find function definitions and their names
    query = Query(PY_LANGUAGE, """
        (function_definition
            name: (identifier) @function.name) @function.def
    """)

    cursor = tree_sitter.QueryCursor(query)
    # Use matches() so each match dict contains perfectly paired name+def nodes
    matches = cursor.matches(tree.root_node)

    class_query = Query(PY_LANGUAGE, """
        (class_definition
            name: (identifier) @class.name) @class.def
    """)
    class_cursor = tree_sitter.QueryCursor(class_query)
    class_matches = class_cursor.matches(tree.root_node)

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
        
        chroma_id = f"{os.path.basename(file_path)}::class::{name}"
        try:
            chroma_collection.upsert(
                documents=[raw_code],
                metadatas=[{
                    "filepath": file_path, "start_line": start_line, 
                    "author": author, "name": name, "type": "class"
                }],
                ids=[chroma_id]
            )
        except Exception:
            pass
            
        cls_data = {
            'name': name,
            'line': start_line,
            'author': author,
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

        # Find calls inside this function's body
        calls = []
        call_query = Query(PY_LANGUAGE, "(call function: (identifier) @callee)")
        call_cursor = tree_sitter.QueryCursor(call_query)
        call_captures = call_cursor.captures(def_node)
        if 'callee' in call_captures:
            for callee_node in call_captures['callee']:
                calls.append(callee_node.text.decode('utf8'))

        author = get_author(file_path, start_line)

        # Check if function belongs to a class
        parent_class = None
        for cls in class_scopes:
            if cls['start_byte'] <= def_node.start_byte and cls['end_byte'] >= def_node.end_byte:
                if not parent_class or (cls['end_byte'] - cls['start_byte'] < parent_class['end_byte'] - parent_class['start_byte']):
                    parent_class = cls
        
        parent_class_name = parent_class['name'] if parent_class else None

        chroma_id = f"{os.path.basename(file_path)}::func::{name}"
        try:
            chroma_collection.upsert(
                documents=[raw_code],
                metadatas=[{
                    "filepath":   file_path,
                    "start_line": start_line,
                    "author":     author,
                    "name":       name,
                    "type":       "function"
                }],
                ids=[chroma_id]
            )
        except Exception as e:
            pass

        functions.append({
            'name':   name,
            'line':   start_line,
            'calls':  list(set(calls)),
            'author': author,
            'parent_class': parent_class_name
        })

    return {
        'file_path': file_path,
        'classes': classes,
        'functions': functions
    }



def get_neo4j_ops(entities, file_path):
    """Return list of (cypher_string, params_dict) tuples.
    Generate explicit File, Class, and Function nodes with CONTAINS hierarchy."""
    norm_path = file_path.replace("\\", "/")
    ops = []
    
    classes = entities['classes']
    functions = entities['functions']

    # 1. File Node
    ops.append((
        "MERGE (file:File {file: $file}) "
        "SET file.name = $name",
        {"file": norm_path, "name": os.path.basename(norm_path)}
    ))

    # 2. Class Nodes & File->Class CONTAINS
    for cls in classes:
        ops.append((
            "MERGE (c:Class {name: $name, file: $file}) "
            "SET c.line = $line, c.author = $author",
            {"name": cls['name'], "file": norm_path, "line": cls['line'], "author": cls['author']}
        ))
        ops.append((
            "MATCH (file:File {file: $file}), (c:Class {name: $name, file: $file}) "
            "MERGE (file)-[:CONTAINS]->(c)",
            {"file": norm_path, "name": cls['name']}
        ))

    # 3. Function Nodes & Hierarchy CONTAINS
    for func in functions:
        ops.append((
            "MERGE (f:Function {name: $name, file: $file}) "
            "SET f.line = $line, f.author = $author",
            {"name": func['name'], "file": norm_path, "line": func['line'], "author": func['author']}
        ))
        
        if func['parent_class']:
            # Method belongs to Class
            ops.append((
                "MATCH (c:Class {name: $cls_name, file: $file}), (f:Function {name: $func_name, file: $file}) "
                "MERGE (c)-[:CONTAINS]->(f)",
                {"cls_name": func['parent_class'], "func_name": func['name'], "file": norm_path}
            ))
        else:
            # Module-level belongs to File
            ops.append((
                "MATCH (file:File {file: $file}), (f:Function {name: $func_name, file: $file}) "
                "MERGE (file)-[:CONTAINS]->(f)",
                {"file": norm_path, "func_name": func['name']}
            ))

    # 4. Function CALLS Function logic
    # Currently we only trace calls between functions to keep it granular.
    defined_names = {f['name'] for f in functions}
    for func in functions:
        for callee in func['calls']:
            if callee in defined_names:
                ops.append((
                    "MATCH (caller:Function {name: $caller_name, file: $file}), "
                    "(callee:Function {name: $callee_name, file: $file}) "
                    "MERGE (caller)-[:CALLS]->(callee)",
                    {
                        "caller_name": func['name'],
                        "callee_name": callee,
                        "file":        norm_path,
                    }
                ))

    return ops


def analyze_project(directory_path):
    """
    Main entry point.
    1. Wipes Neo4j and ChromaDB.
    2. Walks directory_path for all .py files.
    3. Extracts functions and pushes to both DBs.
    """
    print(f"\n=== Analyzing project: {directory_path} ===\n")

    # -- Wipe Neo4j ---------------------------------------------------------
    print("[Neo4j] Clearing existing graph data...")
    try:
        with GraphDatabase.driver(URI, auth=AUTH) as driver:
            with driver.session() as session:
                session.run("MATCH (n) DETACH DELETE n")
        print("[Neo4j] Graph cleared.")
    except Exception as e:
        print(f"[Neo4j] Error clearing graph: {e}")
        raise

    # -- Wipe & recreate ChromaDB collection --------------------------------
    print("[Chroma] Resetting 'codebase_nodes' collection...")
    chroma_client = chromadb.PersistentClient(path="./chroma_data")
    try:
        chroma_client.delete_collection("codebase_nodes")
        print("[Chroma] Deleted existing collection.")
    except Exception:
        print("[Chroma] No existing collection — starting fresh.")
    collection = chroma_client.create_collection(name="codebase_nodes")
    print("[Chroma] Created fresh 'codebase_nodes' collection.")

    # -- Walk directory and process every .py file --------------------------
    # Directories to never recurse into
    SKIP_DIRS = {'.venv', 'venv', 'env', '__pycache__', '.git',
                 'node_modules', '.tox', 'dist', 'build', '.eggs',
                 '.mypy_cache', '.pytest_cache', 'site-packages'}

    py_files = []
    for root, dirs, files in os.walk(directory_path):
        # Prune dirs in-place so os.walk won't descend into them
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fname in files:
            if fname.endswith('.py'):
                py_files.append(os.path.join(root, fname))

    if not py_files:
        print(f"No .py files found in {directory_path}")
        return {"files": 0, "functions": 0}

    print(f"\nFound {len(py_files)} Python file(s). Extracting...\n")

    total_functions = 0
    all_neo4j_ops = []  # list of (cypher, params) tuples

    for fp in py_files:
        print(f"  Parsing: {fp}")
        entities = extract_entities_from_file(fp, collection)
        funcs_len = len(entities['functions'])
        cls_len = len(entities['classes'])
        print(f"    -> {funcs_len} function(s), {cls_len} class(es) found")
        total_functions += funcs_len
        all_neo4j_ops.extend(get_neo4j_ops(entities, fp))

    # -- Push all parameterized queries to Neo4j ----------------------------
    print(f"\n[Neo4j] Executing {len(all_neo4j_ops)} parameterized queries...")
    try:
        with GraphDatabase.driver(URI, auth=AUTH) as driver:
            with driver.session() as session:
                for cypher, params in all_neo4j_ops:
                    session.run(cypher, params)
        print("[Neo4j] Done.")
    except Exception as e:
        print(f"[Neo4j] Error during write: {e}")
        raise

    print(f"\n=== Complete: {len(py_files)} file(s), {total_functions} function(s) ===\n")
    return {"files": len(py_files), "functions": total_functions}


# -- Allow direct CLI usage -------------------------------------------------
if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    analyze_project(target)
