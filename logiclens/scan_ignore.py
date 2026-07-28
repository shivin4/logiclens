"""Rules for skipping third-party / build / ML artifact dirs and noisy files."""

from __future__ import annotations

import os
import re

# Directory names to never descend into (any depth — matched by folder basename).
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        # Python / virtualenv
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".hypothesis",
        "site-packages",
        ".eggs",
        ".ipynb_checkpoints",
        ".conda",
        "conda-bld",
        # VCS / IDE
        ".git",
        ".svn",
        ".hg",
        ".idea",
        ".vscode",
        ".vs",
        # JS / web bundlers & caches
        "node_modules",
        "bower_components",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".angular",
        ".turbo",
        ".parcel-cache",
        ".cache",
        ".vite",
        ".webpack",
        "storybook-static",
        ".vercel",
        ".netlify",
        ".output",
        "out",
        "dist",
        "build",
        "coverage",
        ".nyc_output",
        # Mobile / native build caches (keep android/ & ios/ — they contain app source)
        "Pods",
        "DerivedData",
        ".gradle",
        ".expo",
        ".dart_tool",
        "AppPackages",
        "xcuserdata",
        "build",
        "intermediates",
        "captures",
        # Rust / Go / Java / .NET build output
        "target",
        ".cargo",
        "obj",
        # ML / AI artifacts (large, not source logic)
        "wandb",
        "mlruns",
        "tensorboard_logs",
        "lightning_logs",
        "checkpoints",
        "checkpoint",
        "saved_models",
        "model_weights",
        "weights",
        "artifacts",
        "runs",
        "datasets",
        "dataset",
        "raw_data",
        "processed",
        "embeddings",
        "vectorstore",
        "faiss_index",
        "huggingface",
        ".huggingface",
        # Bundled / vendor
        "vendor",
        "third_party",
        "third-party",
        "external",
        # LogicLens own storage when nested in a repo
        "github_cache",
        "feedback",
    }
)

# Extra: skip dirs whose names contain these (case-insensitive substring).
SKIP_DIR_SUBSTRINGS: tuple[str, ...] = (
    "node_modules",
    "__pycache__",
    ".gradle",
    "egg-info",
    "site-packages",
)

# File basenames (exact, lowercased) to skip entirely.
SKIP_FILE_NAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "npm-shrinkwrap.json",
        "poetry.lock",
        "Pipfile.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
        "Cargo.lock",
        "mix.lock",
        "pubspec.lock",
        "package.json.bak",
    }
)

# Extensions: binaries, models, media, archives (never parse as code).
SKIP_FILE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".min.js",
        ".min.mjs",
        ".min.cjs",
        ".min.jsx",
        ".min.ts",
        ".min.tsx",
        ".bundle.js",
        ".bundle.min.js",
        ".chunk.js",
        ".vendor.js",
        ".cdn.js",
        ".map",
        ".wasm",
        ".onnx",
        ".pt",
        ".pth",
        ".ckpt",
        ".safetensors",
        ".h5",
        ".hdf5",
        ".pb",
        ".tflite",
        ".bin",
        ".pkl",
        ".pickle",
        ".joblib",
        ".npy",
        ".npz",
        ".parquet",
        ".feather",
        ".arrow",
        ".csv",
        ".tsv",
        ".xlsx",
        ".xls",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".rar",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".svg",
        ".mp4",
        ".mp3",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".class",
        ".jar",
        ".war",
        ".aar",
        ".apk",
        ".ipa",
        ".dmg",
        ".iso",
    }
)

# Candidate extensions for web / app / ML repos (extractor registers a subset with parsers).
CANDIDATE_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyw",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".vue",
        ".svelte",
        ".java",
        ".kt",
        ".kts",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".swift",
        ".cpp",
        ".cc",
        ".cxx",
        ".h",
        ".hpp",
        ".hxx",
        ".cs",
        ".dart",
        ".r",
        ".jl",
        ".ipynb",
        ".scala",
        ".ex",
        ".exs",
    }
)

_VUE_SCRIPT_RE = re.compile(
    r"<script[^>]*>([\s\S]*?)</script>",
    re.IGNORECASE,
)
_SVELTE_SCRIPT_RE = _VUE_SCRIPT_RE


def should_skip_dir(dir_name: str) -> bool:
    if dir_name in SKIP_DIR_NAMES:
        return True
    if dir_name.startswith("."):
        return True
    lower = dir_name.lower()
    return any(s in lower for s in SKIP_DIR_SUBSTRINGS)


def prune_walk_dirs(dirs: list[str]) -> None:
    """In-place os.walk prune: drop skip-list and hidden dirs."""
    dirs[:] = [d for d in dirs if not should_skip_dir(d)]


def default_max_js_bytes() -> int:
    raw = os.environ.get("LOGICLENS_MAX_JS_BYTES", "400000")
    try:
        return max(50_000, int(raw))
    except ValueError:
        return 400_000


def default_max_source_bytes() -> int:
    raw = os.environ.get("LOGICLENS_MAX_SOURCE_BYTES", "800000")
    try:
        return max(100_000, int(raw))
    except ValueError:
        return 800_000


def _endswith_any(name: str, suffixes: frozenset[str]) -> bool:
    lower = name.lower()
    return any(lower.endswith(s) for s in suffixes)


def should_skip_parsed_file(file_path: str) -> bool:
    """
    Skip lockfiles, bundles, model weights, and oversized JS/TS sources.

    Override sizes with LOGICLENS_MAX_JS_BYTES / LOGICLENS_MAX_SOURCE_BYTES.
    """
    name = os.path.basename(file_path)
    lower = name.lower()

    if lower in SKIP_FILE_NAMES:
        return True
    if _endswith_any(name, SKIP_FILE_EXTENSIONS):
        return True

    # Generated / vendored path segments
    norm = file_path.replace("\\", "/").lower()
    skip_path_bits = (
        "/node_modules/",
        "/dist/",
        "/build/",
        "/vendor/",
        "/.next/",
        "/coverage/",
        "/checkpoints/",
        "/wandb/",
        "/mlruns/",
        "/__pycache__/",
    )
    if any(b in norm for b in skip_path_bits):
        return True

    ext = os.path.splitext(name)[1].lower()
    if ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte"}:
        try:
            if os.path.getsize(file_path) > default_max_js_bytes():
                return True
        except OSError:
            return True

    if ext in CANDIDATE_SOURCE_EXTENSIONS:
        try:
            if os.path.getsize(file_path) > default_max_source_bytes():
                return True
        except OSError:
            return True

    return False


def should_skip_github_path(path: str) -> bool:
    """Skip paths when syncing a repo via GitHub API."""
    lower = path.replace("\\", "/").lower()
    parts = lower.split("/")
    for part in parts:
        if should_skip_dir(part):
            return True
    ext = os.path.splitext(lower)[1]
    if ext and ext not in CANDIDATE_SOURCE_EXTENSIONS:
        return True
    if os.path.basename(lower) in SKIP_FILE_NAMES:
        return True
    if _endswith_any(os.path.basename(lower), SKIP_FILE_EXTENSIONS):
        return True
    return False


def extract_embedded_script(source: str, ext: str) -> str | None:
    """Pull <script> body from Vue/Svelte SFCs for JS/TS parsing."""
    if ext == ".vue":
        m = _VUE_SCRIPT_RE.search(source)
        return m.group(1).strip() if m else None
    if ext == ".svelte":
        m = _SVELTE_SCRIPT_RE.search(source)
        return m.group(1).strip() if m else None
    return None
