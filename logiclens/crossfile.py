"""Cross-file dependency resolution: imports -> file edges, unresolved calls -> cross-file CALLS."""

from __future__ import annotations

import os
import posixpath
import re
from collections import defaultdict
from typing import Any

# Calls to these names are too generic to link across files by name alone.
GENERIC_NAMES = frozenset(
    {
        "main", "init", "run", "get", "set", "test", "setup", "start", "stop", "close",
        "open", "read", "write", "load", "save", "update", "delete", "create", "build",
        "add", "remove", "name", "value", "data", "call", "apply", "execute", "handle",
        "toString", "equals", "hashCode", "print", "log", "len", "str", "int", "map",
        "filter", "reduce", "push", "pop", "next", "then", "catch", "emit", "send",
    }
)

_PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([^\n]+)$", re.M)
_PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*[\w\.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n]+)", re.M)
_JS_IMPORT = re.compile(
    r"""(?:import\s+(?:[^'"]*?\s+from\s+)?|export\s+[^'"]*?\s+from\s+|require\(\s*|import\(\s*)['"]([^'"]+)['"]"""
)
_JS_NAMED = re.compile(
    r"""import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from\s*['"]([^'"]+)['"]"""
)
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w\.]+)(\.\*)?\s*;", re.M)
_GO_IMPORT_LINE = re.compile(r'"([^"]+)"')
_GO_BLOCK = re.compile(r"import\s*\(([^)]*)\)", re.S)
_GO_SINGLE = re.compile(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"', re.M)
_C_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
_RUBY_REQ = re.compile(r"""^\s*require(?:_relative)?\s+['"]([^'"]+)['"]""", re.M)
_RUST_USE = re.compile(r"^\s*(?:pub\s+)?(?:use|mod)\s+(?:crate::|self::|super::)?([\w:]+)", re.M)
_CS_USING = re.compile(r"^\s*using\s+([\w\.]+)\s*;", re.M)
_PHP_USE = re.compile(r"^\s*use\s+([\w\\]+)", re.M)


def extract_imports(code: str, ext: str) -> list[dict[str, Any]]:
    """Return raw import specs: [{"spec": str, "kind": "py|path|dotted|dir"}]."""
    out: list[dict[str, Any]] = []
    if not code:
        return out
    if ext in (".py", ".ipynb"):
        for m in _PY_FROM.finditer(code):
            mod = m.group(1)
            body = m.group(2).split("#")[0]
            names = [
                n.strip().split(" as ")[0].strip()
                for n in re.split(r"[,\(\)]+", body)
                if n.strip() and n.strip() != "*"
            ]
            out.append({"spec": mod, "kind": "py", "names": names})
            for n in names:
                # "from pkg import submodule" may refer to pkg/submodule.py
                out.append({"spec": (mod if mod.endswith(".") else mod + ".") + n, "kind": "py"})
        for m in _PY_IMPORT.finditer(code):
            for part in m.group(1).split(","):
                part = part.strip().split(" as ")[0].strip()
                if part:
                    out.append({"spec": part, "kind": "py"})
    elif ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte"):
        named: dict[str, list[str]] = {}
        for m in _JS_NAMED.finditer(code):
            lst = named.setdefault(m.group(3), [])
            if m.group(1):
                lst.append(m.group(1))
            for part in (m.group(2) or "").split(","):
                part = part.strip().split(" as ")[0].strip()
                if part:
                    lst.append(part)
        for m in _JS_IMPORT.finditer(code):
            d = {"spec": m.group(1), "kind": "path"}
            if named.get(m.group(1)):
                d["names"] = named[m.group(1)]
            out.append(d)
    elif ext in (".java", ".kt", ".kts", ".scala"):
        for m in _JAVA_IMPORT.finditer(code):
            d = {"spec": m.group(1), "kind": "dotted"}
            if not m.group(2):
                d["names"] = [m.group(1).rsplit(".", 1)[-1]]
            out.append(d)
    elif ext == ".go":
        for m in _GO_BLOCK.finditer(code):
            for s in _GO_IMPORT_LINE.findall(m.group(1)):
                out.append({"spec": s, "kind": "dir"})
        for m in _GO_SINGLE.finditer(code):
            out.append({"spec": m.group(1), "kind": "dir"})
    elif ext in (".cpp", ".cc", ".h", ".hpp"):
        for m in _C_INCLUDE.finditer(code):
            out.append({"spec": m.group(1), "kind": "path"})
    elif ext == ".rb":
        for m in _RUBY_REQ.finditer(code):
            out.append({"spec": m.group(1), "kind": "path"})
    elif ext == ".rs":
        for m in _RUST_USE.finditer(code):
            out.append({"spec": m.group(1).replace("::", "."), "kind": "dotted"})
    elif ext == ".cs":
        for m in _CS_USING.finditer(code):
            out.append({"spec": m.group(1), "kind": "dotted"})
    elif ext == ".php":
        for m in _PHP_USE.finditer(code):
            out.append({"spec": m.group(1).replace("\\", "."), "kind": "dotted"})
    # de-dup, keep order
    seen: set[tuple[str, str]] = set()
    uniq = []
    for i in out:
        k = (i["spec"], i["kind"])
        if k not in seen:
            seen.add(k)
            uniq.append(i)
    return uniq


def _noext(path: str) -> str:
    return posixpath.splitext(path)[0]


class _FileIndex:
    def __init__(self, files: list[str]) -> None:
        self.files = files
        self.noext = {f: _noext(f) for f in files}
        self.by_noext = defaultdict(list)
        self.by_dir = defaultdict(list)
        for f, ne in self.noext.items():
            self.by_noext[ne].append(f)
            self.by_dir[posixpath.dirname(f)].append(f)

    def suffix_match(self, spec_path: str) -> list[str]:
        """Files whose extension-less path ends with spec_path (or spec_path/index|__init__)."""
        spec_path = spec_path.strip("/")
        if not spec_path:
            return []
        hits = []
        tails = ("/" + spec_path, "/" + spec_path + "/index", "/" + spec_path + "/__init__")
        for f, ne in self.noext.items():
            probe = "/" + ne.lstrip("/")
            if any(probe.endswith(t) for t in tails):
                hits.append(f)
        return hits


def resolve_import(spec: dict[str, Any], importer: str, idx: _FileIndex) -> list[str]:
    s = spec["spec"]
    kind = spec["kind"]
    imp_dir = posixpath.dirname(importer)

    if kind == "py":
        if s.startswith("."):
            dots = len(s) - len(s.lstrip("."))
            rest = s.lstrip(".").replace(".", "/")
            base = imp_dir
            for _ in range(dots - 1):
                base = posixpath.dirname(base)
            target = posixpath.normpath(posixpath.join(base, rest)) if rest else base
            hits = idx.by_noext.get(target, []) + idx.by_noext.get(target + "/__init__", [])
            return [h for h in hits if h != importer]
        hits = idx.suffix_match(s.replace(".", "/"))
        return [h for h in hits if h != importer]

    if kind == "path":
        if s.startswith("."):
            target = posixpath.normpath(posixpath.join(imp_dir, s))
            cands = [target, target + "/index"]
            hits: list[str] = []
            for c in cands:
                hits.extend(idx.by_noext.get(_noext(c), []))
                hits.extend(idx.by_noext.get(c, []))
            # allow explicit extension in the spec ("./a.js")
            if not hits:
                hits.extend(idx.by_noext.get(_noext(target), []))
            # C/C++ pairing: header <-> source with same stem
            hits = [h for h in dict.fromkeys(hits) if h != importer]
            return hits
        stripped = re.sub(r"^(@/|~/|\$lib/)", "", s)
        stripped = _noext(stripped) if re.search(r"\.\w{1,4}$", stripped) else stripped
        if stripped.startswith("@") or "/" not in stripped and stripped.isidentifier() and not any(
            stripped == posixpath.basename(f) for f in idx.noext.values()
        ):
            return []
        hits = idx.suffix_match(stripped)
        return [h for h in hits if h != importer]

    if kind == "dotted":
        hits = idx.suffix_match(s.replace(".", "/"))
        if not hits and "." in s:
            # "pkg.Class.member" / static import of member -> try the parent
            hits = idx.suffix_match(s.rsplit(".", 1)[0].replace(".", "/"))
        return [h for h in hits if h != importer]

    if kind == "dir":
        hits = []
        probe = "/" + s.strip("/")
        for d, fs in idx.by_dir.items():
            if ("/" + d.strip("/")).endswith(probe):
                hits.extend(fs)
        return [h for h in hits if h != importer]

    return []


def _c_family_pairs(files: list[str]) -> dict[str, list[str]]:
    """a.h <-> a.cpp in the same directory count as implicitly related."""
    stems = defaultdict(list)
    for f in files:
        if posixpath.splitext(f)[1].lower() in (".h", ".hpp", ".cpp", ".cc"):
            stems[_noext(f)].append(f)
    return {k: v for k, v in stems.items() if len(v) > 1}


def resolve_cross_file(store) -> dict[str, int]:
    """Recompute IMPORTS (File->File) and cross-file CALLS edges from stored per-file metadata.

    Safe to call repeatedly: previously derived edges are removed first.
    """
    store.clear_derived_edges()
    meta = store.load_file_meta()  # {file: {"imports": [...], "calls": {fn: [names]}, "members": {fn: [names]}}}
    files = sorted(meta.keys())
    if not files:
        return {"imports": 0, "calls": 0}

    idx = _FileIndex(files)

    # name -> [(file, node_id)] over classes and functions
    targets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    defined_in_file: dict[str, set[str]] = defaultdict(set)
    for node in store.iter_symbol_nodes():
        targets[node["name"]].append((node["file"], node["id"]))
        defined_in_file[node["file"]].add(node["name"])

    imported: dict[str, set[str]] = defaultdict(set)
    # explicit bindings: file -> {local name: {files that the import resolved to}}
    bound: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    n_imports = 0
    for f, m in meta.items():
        for spec in m.get("imports", []):
            hits = [t for t in resolve_import(spec, f, idx) if t != f and t in meta]
            for tgt in hits:
                imported[f].add(tgt)
            for nm in spec.get("names") or []:
                for tgt in hits:
                    if nm in defined_in_file[tgt]:
                        bound[f][nm].add(tgt)
    for stem, group in _c_family_pairs(files).items():
        for a in group:
            for b in group:
                if a != b:
                    imported[a].add(b)

    for f, tgts in imported.items():
        for t in tgts:
            store.add_edge(store.file_id(f), store.file_id(t), "IMPORTS")
            n_imports += 1

    def _closest(cands: list[tuple[str, str]], src_file: str) -> list[tuple[str, str]]:
        """Of several candidate targets keep only those sharing the longest directory prefix with the caller."""
        src_parts = posixpath.dirname(src_file).split("/")
        def score(cf: str) -> int:
            n = 0
            for a, b in zip(src_parts, posixpath.dirname(cf).split("/")):
                if a != b:
                    break
                n += 1
            return n
        best = max(score(cf) for cf, _ in cands)
        return [(cf, cid) for cf, cid in cands if score(cf) == best]

    def _pick(c: str, src_file: str, cands: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Resolve callee name c from src_file. Prefers explicit imports; refuses to guess among ties."""
        # 1. explicitly bound by `from x import c` / `import {c} from` -> exact
        b = bound[src_file].get(c)
        if b:
            return [(cf, cid) for cf, cid in cands if cf in b]
        # 2. defined in an imported file
        in_imported = [(cf, cid) for cf, cid in cands if cf in imported[src_file]]
        if len(in_imported) == 1:
            return in_imported
        if len(in_imported) > 1:
            near = _closest(in_imported, src_file)
            return near if len(near) == 1 else []  # ambiguous -> no edge beats a wrong edge
        # 3. same directory, unique
        same_dir = [(cf, cid) for cf, cid in cands if posixpath.dirname(cf) == posixpath.dirname(src_file) and cf != src_file]
        if len(same_dir) == 1:
            return same_dir
        # 4. globally unique, distinctive name
        if len(cands) == 1 and cands[0][0] != src_file and len(c) >= 4 and c not in GENERIC_NAMES:
            return cands
        return []

    n_calls = 0
    for f, m in meta.items():
        for fn_name, callees in (m.get("calls") or {}).items():
            src_id = store.function_id(f, fn_name)
            for c in callees:
                if c in defined_in_file[f] and c not in bound[f]:
                    continue  # same-file call already linked at parse time
                cands = targets.get(c)
                if not cands:
                    continue
                for cf, cid in _pick(c, f, [x for x in cands if x[0] != f]):
                    store.add_edge(src_id, cid, "CALLS")
                    n_calls += 1
        # obj.method() / module.func(): only trusted when the target lives in an imported file
        for fn_name, names in (m.get("members") or {}).items():
            src_id = store.function_id(f, fn_name)
            for c in names:
                if c in GENERIC_NAMES or (c in defined_in_file[f] and c not in bound[f]):
                    continue
                cands = [x for x in targets.get(c, []) if x[0] in imported[f] and x[0] != f]
                if len(cands) > 1:
                    cands = _closest(cands, f)
                    if len(cands) > 1:
                        continue
                for cf, cid in cands:
                    store.add_edge(src_id, cid, "CALLS")
                    n_calls += 1
    return {"imports": n_imports, "calls": n_calls}
