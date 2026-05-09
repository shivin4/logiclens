"""Embedded graph storage (SQLite) replacing Neo4j for desktop/local MVP."""

from __future__ import annotations

import hashlib
import os
import json
import sqlite3
from pathlib import Path
from typing import Any

from logiclens.config import normalize_project_file_path


def _stable_id(label: str, **keys: str | int | float | None) -> str:
    payload = json.dumps({"label": label, **keys}, sort_keys=True, default=str)
    h = hashlib.sha256(payload.encode()).hexdigest()[:28]
    return f"{label}_{h}"


class SqliteGraphStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    name TEXT,
                    file TEXT,
                    line INTEGER,
                    author TEXT,
                    handler TEXT,
                    vulnerabilities TEXT
                );
                CREATE TABLE IF NOT EXISTS edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src TEXT NOT NULL,
                    dst TEXT NOT NULL,
                    rel_type TEXT NOT NULL,
                    UNIQUE(src, dst, rel_type)
                );
                CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
                CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
                CREATE INDEX IF NOT EXISTS idx_nodes_label ON nodes(label);
                """
            )

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM nodes")

    def delete_file_subgraph(self, norm_path: str) -> int:
        """
        Remove all nodes with file == norm_path and any incident edges.
        Returns the number of nodes removed.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM nodes WHERE file = ?", (norm_path,)
            ).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"DELETE FROM edges WHERE src IN ({placeholders}) OR dst IN ({placeholders})",
                ids + ids,
            )
            conn.execute(
                f"DELETE FROM nodes WHERE id IN ({placeholders})",
                ids,
            )
        return len(ids)

    def prune_orphan_api_routes(self) -> int:
        """Remove APIRoute nodes that have no edges (e.g. after incremental file removal)."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM nodes
                WHERE label = 'APIRoute'
                  AND id NOT IN (SELECT src FROM edges)
                  AND id NOT IN (SELECT dst FROM edges)
                """
            )
            return int(cur.rowcount) if cur.rowcount is not None else 0

    def node_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()
            return int(row["c"]) if row else 0

    def upsert_node(
        self,
        node_id: str,
        label: str,
        name: str | None = None,
        file: str | None = None,
        line: int | None = None,
        author: str | None = None,
        handler: str | None = None,
        vulnerabilities: list[str] | None = None,
    ) -> str:
        vuln_json = json.dumps(vulnerabilities or [])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO nodes (id, label, name, file, line, author, handler, vulnerabilities)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label=excluded.label,
                    name=COALESCE(excluded.name, nodes.name),
                    file=COALESCE(excluded.file, nodes.file),
                    line=COALESCE(excluded.line, nodes.line),
                    author=COALESCE(excluded.author, nodes.author),
                    handler=COALESCE(excluded.handler, nodes.handler),
                    vulnerabilities=excluded.vulnerabilities
                """,
                (node_id, label, name, file, line, author, handler, vuln_json),
            )
        return node_id

    def add_edge(self, src: str, dst: str, rel_type: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO edges (src, dst, rel_type) VALUES (?, ?, ?)
                """,
                (src, dst, rel_type),
            )

    # --- ID builders (deterministic, same as merge keys in extractor) ---

    def file_id(self, norm_path: str) -> str:
        return _stable_id("File", file=norm_path)

    def class_id(self, norm_path: str, name: str) -> str:
        return _stable_id("Class", file=norm_path, name=name)

    def function_id(self, norm_path: str, name: str) -> str:
        return _stable_id("Function", file=norm_path, name=name)

    def api_route_id(self, path: str) -> str:
        return _stable_id("APIRoute", name=path)

    # --- Read API (Flask) ---

    def row_to_node(self, row: sqlite3.Row) -> dict[str, Any]:
        vulns: list[str] = []
        if row["vulnerabilities"]:
            try:
                vulns = json.loads(row["vulnerabilities"])
            except json.JSONDecodeError:
                vulns = []
        return {
            "id": row["id"],
            "type": row["label"],
            "label": row["name"] or "Unknown",
            "file": row["file"] or "",
            "line": row["line"] or 0,
            "author": row["author"] or "",
            "handler": row["handler"] or "",
            "vulnerabilities": vulns,
        }

    def fetch_full_graph(self) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, str]] = []
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT src, dst, rel_type FROM edges"
            ):
                for nid in (row["src"], row["dst"]):
                    if nid not in nodes:
                        n = conn.execute(
                            "SELECT * FROM nodes WHERE id = ?", (nid,)
                        ).fetchone()
                        if n:
                            nodes[nid] = self.row_to_node(n)
                edges.append(
                    {
                        "from": row["src"],
                        "to": row["dst"],
                        "label": row["rel_type"],
                    }
                )
            for row in conn.execute(
                """
                SELECT n.* FROM nodes n
                WHERE NOT EXISTS (SELECT 1 FROM edges e WHERE e.src = n.id OR e.dst = n.id)
                """
            ):
                nid = row["id"]
                if nid not in nodes:
                    nodes[nid] = self.row_to_node(row)
        return nodes, edges

    def trace_calls(self, node_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Match Neo4j trace: incoming = any edge into node; outgoing = CALLS from node."""
        incoming: list[dict[str, Any]] = []
        outgoing: list[dict[str, Any]] = []
        with self._connect() as conn:
            for row in conn.execute(
                """
                SELECT m.* FROM edges e
                JOIN nodes m ON m.id = e.src
                WHERE e.dst = ?
                """,
                (node_id,),
            ):
                incoming.append(self.row_to_node(row))
            for row in conn.execute(
                """
                SELECT m.* FROM edges e
                JOIN nodes m ON m.id = e.dst
                WHERE e.src = ? AND e.rel_type = 'CALLS'
                """,
                (node_id,),
            ):
                outgoing.append(self.row_to_node(row))
        return incoming, outgoing

    def list_function_names(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name FROM nodes WHERE label = 'Function' AND name IS NOT NULL
                ORDER BY name
                """
            ).fetchall()
        return [r["name"] for r in rows if r["name"]]

    def callers_of_function_name(self, function_name: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT caller.name AS caller_name FROM edges e
                JOIN nodes caller ON caller.id = e.src
                JOIN nodes target ON target.id = e.dst
                WHERE e.rel_type = 'CALLS'
                  AND target.label = 'Function'
                  AND target.name = ?
                  AND caller.label = 'Function'
                """,
                (function_name,),
            ).fetchall()
        return [r["caller_name"] for r in rows if r["caller_name"]]


def apply_entities_to_store(store: SqliteGraphStore, entities: dict[str, Any], file_path: str) -> None:
    """Mirror get_neo4j_ops semantics using SqliteGraphStore."""
    norm_path = normalize_project_file_path(file_path)
    classes = entities["classes"]
    functions = entities["functions"]

    fid = store.file_id(norm_path)
    store.upsert_node(
        fid,
        "File",
        name=os.path.basename(norm_path),
        file=norm_path,
        line=0,
        author="",
        handler="",
        vulnerabilities=[],
    )

    for cls in classes:
        cid = store.class_id(norm_path, cls["name"])
        store.upsert_node(
            cid,
            "Class",
            name=cls["name"],
            file=norm_path,
            line=cls["line"],
            author=cls["author"],
            handler="",
            vulnerabilities=cls.get("vulnerabilities", []),
        )
        store.add_edge(fid, cid, "CONTAINS")

    defined_names = {f["name"] for f in functions}
    for func in functions:
        fnid = store.function_id(norm_path, func["name"])
        store.upsert_node(
            fnid,
            "Function",
            name=func["name"],
            file=norm_path,
            line=func["line"],
            author=func["author"],
            handler="",
            vulnerabilities=func.get("vulnerabilities", []),
        )
        if func.get("parent_class"):
            cid = store.class_id(norm_path, func["parent_class"])
            store.add_edge(cid, fnid, "CONTAINS")
        else:
            store.add_edge(fid, fnid, "CONTAINS")

    for func in functions:
        caller_id = store.function_id(norm_path, func["name"])
        for callee in func["calls"]:
            if callee in defined_names:
                callee_id = store.function_id(norm_path, callee)
                store.add_edge(caller_id, callee_id, "CALLS")

    for func in functions:
        fnid = store.function_id(norm_path, func["name"])
        for route in func.get("api_exposures", []):
            aid = store.api_route_id(route)
            store.upsert_node(
                aid,
                "APIRoute",
                name=route,
                file=norm_path,
                line=func["line"],
                author=func["author"],
                handler=func["name"],
                vulnerabilities=[],
            )
            store.add_edge(fnid, aid, "EXPOSES_API")
        for route in func.get("api_calls", []):
            aid = store.api_route_id(route)
            store.upsert_node(
                aid,
                "APIRoute",
                name=route,
                file=None,
                line=None,
                author="",
                handler="",
                vulnerabilities=[],
            )
            store.add_edge(fnid, aid, "CALLS_API")
