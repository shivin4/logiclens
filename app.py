from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
print(api_key)  # test once

from flask import Flask, request, jsonify, render_template
import os
import ast
import google.generativeai as genai
from neo4j import GraphDatabase
from extractor import analyze_project

app = Flask(__name__)

# Neo4j connection details (shared with extractor.py)
URI = "neo4j://127.0.0.1:7687"
AUTH = ("neo4j", "Password123")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json()
    if not data or "path" not in data:
        return jsonify({"error": "Missing 'path' in request body."}), 400

    directory_path = data["path"].strip()

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
    path = request.args.get("path")
    if not path:
        return jsonify({"error": "Missing 'path' parameter."}), 400
    
    tree = get_dir_tree(path)
    if tree is None:
        return jsonify({"error": "Invalid directory path or unable to read."}), 400
        
    return jsonify({"status": "success", "tree": tree})

def extract_code(filepath, node_name, node_type):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            code = f.read()
            
        if node_type == "File":
            return code
            
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if node_type == "Class" and isinstance(node, ast.ClassDef):
                if node.name == node_name:
                    return ast.get_source_segment(code, node)
            elif node_type == "Function" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == node_name:
                    return ast.get_source_segment(code, node)
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
                out_res = session.run("MATCH (n)-[r]->(m) WHERE elementId(n) = $id RETURN m", {"id": node_id})
                for record in out_res:
                    m = record["m"]
                    outgoing.append({
                        "id": m.element_id, 
                        "name": m.get("name", ""), 
                        "file": m.get("file", ""), 
                        "line": m.get("line", 0), 
                        "author": m.get("author", "")
                    })
                    
                in_res = session.run("MATCH (m)-[r]->(n) WHERE elementId(n) = $id RETURN m", {"id": node_id})
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
        
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({
            "status": "success", 
            "explanation": "Google Gemini API Key is missing. Please set the GEMINI_API_KEY environment variable in your terminal to enable AI explanations."
        })
        
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        
        prompt = f"Explain exactly what this Python {node_type} does. Be concise, use a maximum of 3 sentences. Write it in plain English suitable for a developer overview:\n\n```python\n{code_segment[:8000]}\n```"
        response = model.generate_content(prompt)
        return jsonify({"status": "success", "explanation": response.text})
    except Exception as e:
        return jsonify({"error": f"AI Generation Failed: {e}"}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
