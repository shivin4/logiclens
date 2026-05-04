from dotenv import load_dotenv
import os
import git
import traceback

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print(api_key)  # test once

from flask import Flask, request, jsonify, render_template, Response
import ast
import google.generativeai as genai
from neo4j import GraphDatabase
from extractor import analyze_project, LANGUAGES, CONFIGS
import tree_sitter
from tree_sitter import Parser, Query
import chromadb

app = Flask(__name__, static_folder='logo', static_url_path='/logo')

# Neo4j connection details (shared with extractor.py)
URI = "neo4j://127.0.0.1:7687"
AUTH = (os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", "password123"))

current_repo_path = None


@app.route("/")
def index():
    return render_template("index.html")


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
    nodes = {}
    edges = []

    try:
        with GraphDatabase.driver(URI, auth=AUTH) as driver:
            with driver.session() as session:
                # Fetch all relationships (covers all connected nodes)
                result = session.run("MATCH (n)-[r]->(m) RETURN n, r, m")
                for record in result:
                    n = record["n"]
                    m = record["m"]
                    r = record["r"]

                    n_id = n.element_id
                    m_id = m.element_id

                    if n_id not in nodes:
                        n_type = list(n.labels)[0] if n.labels else "Unknown"
                        nodes[n_id] = {
                            "id":    n_id,
                            "type":  n_type,
                            "label": n.get("name", "Unknown"),
                            "file":  n.get("file", ""),
                            "line":  n.get("line", 0),
                            "author": n.get("author", ""),
                        }
                    if m_id not in nodes:
                        m_type = list(m.labels)[0] if m.labels else "Unknown"
                        nodes[m_id] = {
                            "id":    m_id,
                            "type":  m_type,
                            "label": m.get("name", "Unknown"),
                            "file":  m.get("file", ""),
                            "line":  m.get("line", 0),
                            "author": m.get("author", ""),
                        }

                    edges.append({
                        "from":  n_id,
                        "to":    m_id,
                        "label": r.type,
                    })

                # Also fetch isolated nodes (no relationships)
                result2 = session.run(
                    "MATCH (n) WHERE NOT (n)--() RETURN n"
                )
                for record in result2:
                    n = record["n"]
                    n_id = n.element_id
                    if n_id not in nodes:
                        n_type = list(n.labels)[0] if n.labels else "Unknown"
                        nodes[n_id] = {
                            "id":    n_id,
                            "type":  n_type,
                            "label": n.get("name", "Unknown"),
                            "file":  n.get("file", ""),
                            "line":  n.get("line", 0),
                            "author": n.get("author", ""),
                        }

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "nodes": list(nodes.values()),
        "edges": edges,
    })


def get_dir_tree(path):
    ignores = {'.git', '__pycache__', '.venv', 'venv', 'node_modules', 'chroma_data', '.idea', '.vscode'}
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
            name_node = match_dict.get('class.name') if node_type == "Class" else match_dict.get('function.name')
            def_node = match_dict.get('class.def') if node_type == "Class" else match_dict.get('function.def')
            
            if not name_node or not def_node:
                continue
                
            name_node = name_node[0] if isinstance(name_node, list) else name_node
            def_node = def_node[0] if isinstance(def_node, list) else def_node
            
            extracted_name = name_node.text.decode('utf8')
            print(f"[DEBUG] Found extracted_name: {extracted_name}")
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

    incoming = []
    outgoing = []

    try:
        with GraphDatabase.driver(URI, auth=AUTH) as driver:
            with driver.session() as session:
                out_res = session.run("MATCH (n)-[r:CALLS]->(m) WHERE elementId(n) = $id RETURN m", {"id": node_id})
                for record in out_res:
                    m = record["m"]
                    outgoing.append({
                        "id": m.element_id, 
                        "name": m.get("name", ""), 
                        "file": m.get("file", ""), 
                        "line": m.get("line", 0), 
                        "author": m.get("author", "")
                    })
                    
                in_res = session.run("MATCH (m)-[r:CALLS]->(n) WHERE elementId(n) = $id RETURN m", {"id": node_id})
                for record in in_res:
                    m = record["m"]
                    incoming.append({
                        "id": m.element_id, 
                        "name": m.get("name", ""), 
                        "file": m.get("file", ""), 
                        "line": m.get("line", 0), 
                        "author": m.get("author", "")
                    })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "success", "incoming": incoming, "outgoing": outgoing})

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
            "explanation": "Groq API Key is missing. Please set the GROQ_API_KEY environment variable in your terminal to enable AI explanations."
        })
        
    try:
        import urllib.request
        import urllib.error
        import json
        
        prompt = f"Explain exactly what this Python {node_type} does. Be concise, use a maximum of 2 sentences. Write it in plain English suitable for a developer overview. At the very end of your response, add a new line with exactly 'CONFIDENCE: X' where X is a score from 1-100 indicating how certain you are of this explanation.\n\n```python\n{code_segment[:8000]}\n```"
        
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
            text = json.loads(res_body)["choices"][0]["message"]["content"]
        
        parts = text.split("CONFIDENCE:")
        explanation = parts[0].strip()
        confidence = parts[1].strip() if len(parts) > 1 else "N/A"
        
        return jsonify({"status": "success", "explanation": explanation, "confidence": confidence})
    except Exception as e:
        return jsonify({"error": f"AI Generation Failed: {e}"}), 500

@app.route("/api/check_env", methods=["GET"])
def api_check_env():
    has_key = bool(os.environ.get("GROQ_API_KEY"))
    return jsonify({"groq_api_key_present": has_key})

@app.route("/api/functions", methods=["GET"])
def api_functions():
    try:
        with GraphDatabase.driver(URI, auth=AUTH) as driver:
            with driver.session() as session:
                records = session.run("MATCH (f:Function) RETURN f.name AS name ORDER BY f.name")
                names = [r["name"] for r in records if r["name"]]
        return jsonify({"functions": names})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/whatif", methods=["POST"])
def api_whatif():
    data = request.get_json()
    if not data or "function" not in data:
        return jsonify({"error": "Missing 'function' in request body."}), 400

    target = data["function"].strip()
    from whatif_engine import run_whatif_engine
    
    # Return streaming response
    return Response(
        run_whatif_engine(target),
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
    # Get last 15 commits
    for c in repo.iter_commits('HEAD', max_count=15):
        commits.append({
            "hash": c.hexsha[:7],
            "message": c.summary,
            "author": c.author.name,
            "date": c.authored_datetime.strftime("%b %d, %Y")
        })
        
    # Calculate churn (most modified files in last 50 commits)
    churn = {}
    try:
        for c in repo.iter_commits('HEAD', max_count=50):
            for file in c.stats.files.keys():
                # Only care about actual code files
                if file.endswith(('.py', '.js', '.ts', '.java', '.go', '.cpp')):
                    churn[file] = churn.get(file, 0) + 1
    except Exception:
        pass # Ignore diff parsing errors on weird repos
            
    # Sort and take top 10
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
    
    try:
        chroma_client = chromadb.PersistentClient(path="./chroma_data")
        try:
            collection = chroma_client.get_collection(name="codebase_nodes")
        except Exception:
            return jsonify({"error": "ChromaDB collection not found. Please analyze a project first."}), 404
            
        results = collection.query(query_texts=[q], n_results=5)
        
        matches = []
        if results and 'ids' in results and results['ids'] and len(results['ids'][0]) > 0:
            for i in range(len(results['ids'][0])):
                dist = results['distances'][0][i] if 'distances' in results and results['distances'] else 0
                matches.append({
                    "id": results['ids'][0][i],
                    "code_snippet": results['documents'][0][i],
                    "metadata": results['metadatas'][0][i],
                    "similarity": 1.0 - dist
                })
                
        return jsonify({"status": "success", "results": matches})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
