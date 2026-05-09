"""Rules for skipping third-party / build dirs when walking source trees."""

from __future__ import annotations

import os

# Directory names to never descend into (matches any folder name in the tree).
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".git",
        "node_modules",
        ".tox",
        "dist",
        "build",
        ".eggs",
        ".mypy_cache",
        ".pytest_cache",
        "site-packages",
        # Bundled / vendored frontend & tooling
        "vendor",
        "bower_components",
        ".next",
        ".nuxt",
        # Next static export, etc. (omit if you keep source under `out/`)
        "out",
        "coverage",
        ".turbo",
        ".parcel-cache",
        ".cache",
        "chroma_data",
    }
)


def prune_walk_dirs(dirs: list[str]) -> None:
    """In-place os.walk prune: drop skip-list and hidden dirs."""
    dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith(".")]


def default_max_js_bytes() -> int:
    raw = os.environ.get("LOGICLENS_MAX_JS_BYTES", "400000")
    try:
        return max(50_000, int(raw))
    except ValueError:
        return 400_000


def should_skip_parsed_file(file_path: str) -> bool:
    """
    Skip minified bundles and huge JS/TS files (CDN scripts, webpack chunks).

    Override max size with LOGICLENS_MAX_JS_BYTES (default 400000).
    """
    name = os.path.basename(file_path)
    lower = name.lower()

    min_ext = (
        ".min.js",
        ".min.mjs",
        ".min.cjs",
        ".min.jsx",
        ".min.ts",
        ".min.tsx",
    )
    if lower.endswith(min_ext):
        return True
    if lower.endswith(
        (".bundle.js", ".bundle.min.js", ".chunk.js", ".vendor.js", ".cdn.js")
    ):
        return True

    ext = os.path.splitext(name)[1].lower()
    if ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        try:
            if os.path.getsize(file_path) > default_max_js_bytes():
                return True
        except OSError:
            return True

    return False
